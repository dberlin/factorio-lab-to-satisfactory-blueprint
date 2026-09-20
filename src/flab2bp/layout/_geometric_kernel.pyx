# cython: language_level=3, boundscheck=False, wraparound=False, initializedcheck=False
# distutils: language = c++
"""Directed affine-interval wavefront; no cell-priority search or A* fallback."""
from time import monotonic
from math import isfinite

from .geometric_motion import MotionStep, MotionWitness, validate_motion


class _MotionBudget(Exception):
    pass


class _MotionCancelled(Exception):
    pass
class _MotionPreparation:
    def __init__(self, limit, deadline, cancelled):
        self.limit = limit
        self.deadline = deadline
        self.cancelled = cancelled
        self.work = 0
        self.checks = 0

    def poll(self, force=False):
        self.checks += 1
        if (force or self.checks % 4096 == 1) and self.cancelled is not None and self.cancelled():
            raise _MotionCancelled
        if (force or self.checks % 64 == 1) and self.deadline is not None and monotonic() >= self.deadline:
            raise _MotionBudget

    def __call__(self):
        self.poll()
        if self.work >= self.limit:
            raise _MotionBudget
        self.work += 1




def _motion_interrupted(status, work, began):
    return (status, None, None, (), {
        "charged_work": work, "prepared_cells": 0, "interval_pops": 0,
        "labels": 0, "offers": 0, "intersections": 0, "profile_scans": 0,
        "certified_edges": 0, "retained_native_bytes": 0,
        "copied_history_cells": 0, "preparation_s": monotonic() - began,
        "search_s": 0.0, "certification_s": 0.0,
    }, None)

from cpython.ref cimport PyObject
from libcpp.vector cimport vector

cdef extern from *:
    r"""
    #include <Python.h>
    #include <algorithm>
    #include <array>
    #include <chrono>
    #include <cmath>
    #include <cstdint>
    #include <cstring>
    #include <limits>
    #include <map>
    #include <memory>
    #include <mutex>
    #include <queue>
    #include <stdexcept>
    #include <unordered_map>
    #include <vector>

    namespace flab_geometry {
    using Index = std::int64_t;
    using Clock = std::chrono::steady_clock;
    constexpr double infinity = std::numeric_limits<double>::infinity();
    struct Move { int dx, dy, dz; bool via; double cost; int motion = -1; };
    struct MotionBox { int xlo, xhi, ylo, yhi, zlo, zhi; };

    std::vector<MotionBox> free_motion_boxes(
            const MotionBox& domain, const std::vector<MotionBox>& forbidden) {
        if (forbidden.empty()) return {domain};
        const Index nx = Index(domain.xhi) - domain.xlo + 1;
        const Index ny = Index(domain.yhi) - domain.ylo + 1;
        const Index nz = Index(domain.zhi) - domain.zlo + 1;
        if (nx <= 0 || ny <= 0 || nz <= 0) return {};
        std::vector<unsigned char> flags;
        if (std::size_t(nx) > flags.max_size() / std::size_t(ny) / std::size_t(nz))
            throw std::length_error("motion guard domain is too large");
        flags.assign(std::size_t(nx) * ny * nz, 1);
        for (const MotionBox& box : forbidden) {
            const Index xlo = std::max(domain.xlo, box.xlo);
            const Index xhi = std::min(domain.xhi, box.xhi);
            const Index ylo = std::max(domain.ylo, box.ylo);
            const Index yhi = std::min(domain.yhi, box.yhi);
            const Index zlo = std::max(domain.zlo, box.zlo);
            const Index zhi = std::min(domain.zhi, box.zhi);
            if (xlo > xhi || ylo > yhi || zlo > zhi) continue;
            for (Index x = xlo - domain.xlo; x <= xhi - domain.xlo; ++x) {
                for (Index y = ylo - domain.ylo; y <= yhi - domain.ylo; ++y) {
                    auto start = flags.begin() + ((x * ny + y) * nz + (zlo - domain.zlo));
                    std::fill(start, start + (zhi - zlo + 1), 0);
                }
            }
        }
        // Match the original x/y/z traversal and merge only equal full rows.
        // Never extend a box across a missing x slice, even if its key recurs.
        using RowSpan = std::array<int, 4>;
        std::map<RowSpan, std::size_t> previous, current;
        std::vector<MotionBox> boxes;
        for (Index x = 0; x < nx; ++x) {
            current.clear();
            Index y = 0;
            while (y < ny) {
                const unsigned char* row = flags.data() + (x * ny + y) * nz;
                Index after = y + 1;
                while (after < ny &&
                       std::memcmp(row, flags.data() + (x * ny + after) * nz, nz) == 0)
                    ++after;
                Index z = 0;
                while (z < nz) {
                    while (z < nz && !row[z]) ++z;
                    if (z == nz) break;
                    const Index first = z;
                    while (z < nz && row[z]) ++z;
                    const RowSpan key = {
                        int(domain.ylo + y), int(domain.ylo + after - 1),
                        int(domain.zlo + first), int(domain.zlo + z - 1)
                    };
                    const auto found = previous.find(key);
                    std::size_t index;
                    if (found == previous.end()) {
                        index = boxes.size();
                        boxes.push_back({
                            int(domain.xlo + x), int(domain.xlo + x),
                            key[0], key[1], key[2], key[3]
                        });
                    } else {
                        index = found->second;
                        boxes[index].xhi = int(domain.xlo + x);
                    }
                    current.emplace(key, index);
                }
                y = after;
            }
            previous.swap(current);
        }
        return boxes;
    }
    struct MotionEdge { int source, move, target, guard, action; };
    struct MotionEndpoint { Index cell; int state, action; };
    struct Policy {
        bool enabled = false;
        std::vector<MotionEdge> edges;
        std::vector<std::vector<MotionBox>> guards;
        std::vector<MotionEndpoint> initial, accepting;
    };
    struct MotionTrace { Index path_index; int edge, primitive, source, target, width; };
    struct Extra { Index source, target; double cost; };
    struct Run { int lo, hi; double price; bool free; };
    struct Segment { int lo, hi; Index label; };
    struct Label {
        int y, z, lo, hi;
        // Preserve affine sums across differently segmented paths. Rounding
        // each interval back to double manufactures strict improvements on
        // equal-cost plateaus and repeatedly fractures their envelopes.
        long double cost, slope;
        Index parent;
        int fixed, dx;
        bool via, horizontal;
        // Real labels cover one uniform row-price run. A reverse edge pays
        // this source toll: it is the original forward landing price.
        double toll = 0.0;
        int state = 0, edge = -1, primitive = -1;
        long double value(int x) const { return cost + slope * (x - lo); }
    };
    struct TerminalGoal { int x, action; };
    struct StateRow {
        std::vector<Segment> envelope;
        std::vector<TerminalGoal> goals;
    };
    struct Row {
        std::vector<Run> runs;
        std::vector<Segment> envelope;
        std::vector<Extra> extras;
        std::vector<int> goals;
    };
    using StateRows = std::unordered_map<int, StateRow>;
    struct Goal { int x, y, z; double toll; };
    struct GoalSegment { int lo, hi, y, y_hi, z; double toll; };
    struct Entry { long double lower; int near; Index label; int horizontal = 0; };
    struct Later {
        bool operator()(const Entry& a, const Entry& b) const {
            if (a.lower != b.lower) return a.lower > b.lower;
            if (a.near != b.near) return a.near > b.near;
            return a.label > b.label;
        }
    };
    struct Reach { int y, z, lo, hi; };
    struct Answer {
        int status = 2;
        std::vector<Index> path;
        std::vector<Reach> reached;
        std::vector<MotionTrace> motion;
        int initial_state = -1, final_state = -1, final_action = -1;
        double cost = infinity;
        Index work = 0, prepared_cells = 0, interval_pops = 0;
        Index labels = 0, offers = 0, intersections = 0, profile_scans = 0;
        Index certified_edges = 0, memory_bytes = 0;
        double preparation_s = 0, search_s = 0, certification_s = 0;
    };
    struct Limit {};
    struct Cancelled {};

    struct DistanceProfile {
        int extent, goal_level;
        std::vector<std::vector<Move>> topology;
        std::vector<long double> costs;
        std::vector<std::vector<int>> corners;
        // Costs are nondecreasing from this distance onward, per source level.
        std::vector<int> monotone_from;
        bool matches(const std::vector<std::vector<Move>>& candidate, int distance, int goal) const {
            if (extent < distance || goal_level != goal || topology.size() != candidate.size()) return false;
            for (std::size_t z = 0; z < topology.size(); ++z) {
                if (topology[z].size() != candidate[z].size()) return false;
                for (std::size_t i = 0; i < topology[z].size(); ++i) {
                    const Move& a = topology[z][i]; const Move& b = candidate[z][i];
                    if (a.dx != b.dx || a.dy != b.dy || a.dz != b.dz || a.via != b.via || a.cost != b.cost)
                        return false;
                }
            }
            return true;
        }
    };
    // Only immutable movement topology is shared. Occupancy, congestion,
    // endpoints and query-local profile preparation are never cached.
    std::mutex distance_cache_mutex;
    std::vector<std::shared_ptr<const DistanceProfile>> distance_cache;

    class Wave {
        int nx, ny, nz;
        bool transposed;
        const unsigned char* flags;
        const double* history;
        PyObject* history_list;
        const double* present;
        bool charge_occupied_cells;
        PyObject* cancelled;
        bool reverse_reach;
        bool backwards;
        double pressure;
        long double xy_price = 0.0, z_price = 0.0;
        bool distance_is_linear = false;
        const std::vector<std::vector<Move>>& forward_moves;
        const std::vector<Extra>& forward_extras;
        std::vector<std::vector<Move>> backward_moves;
        std::vector<Extra> backward_extras;
        const std::vector<std::vector<Move>>& moves;
        const std::vector<Index>& starts;
        const std::vector<Index>& goal_indices;
        const std::vector<Extra>& extras;
        const Policy* policy;
        std::vector<StateRows> state_rows;
        std::unordered_map<int, std::vector<int>> state_edges;
        Index limit;
        Clock::time_point deadline;
        bool timed;
        unsigned checks = 0;
        std::vector<Row> rows;
        std::vector<Label> labels;
        std::vector<Segment> patch;
        std::vector<Run> profile_patch;
        std::vector<Goal> goals;
        std::vector<GoalSegment> goal_segments;
        std::vector<std::shared_ptr<const DistanceProfile>> distance_profiles;
        std::vector<std::vector<Move>> distance_topology;
        std::priority_queue<Entry, std::vector<Entry>, Later> queue;
        Answer answer;
        long double best = infinity;
        Index goal_label = -1;
        int goal_x = 0;

        Index index(int x, int y, int z) const {
            return (transposed ? Index(y) * nx + x : Index(x) * ny + y) * nz + z;
        }
        void cell(Index i, int& x, int& y, int& z) const {
            z = int(i % nz); i /= nz;
            if (transposed) { x = int(i % nx); y = int(i / nx); }
            else { y = int(i % ny); x = int(i / ny); }
        }
        std::vector<Segment>& envelope(int y, int z, int state) {
            return policy ? state_rows[y * nz + z][state].envelope : rows[y * nz + z].envelope;
        }
        bool closure_edge(const MotionEdge& edge) const {
            return edge.source == edge.target && edge.guard < 0 && edge.action < 0;
        }
        double horizontal_move(const Label& source, int sign, int& primitive, int& edge) {
            double base = infinity;
            const auto& level = moves[source.z];
            for (int i = 0; i < int(level.size()); ++i) {
                check();
                const Move& move = level[i];
                if (move.dx != sign || move.dy || move.dz || move.via || move.cost >= base) continue;
                if (!policy) { base = move.cost; primitive = i; continue; }
                const auto found = state_edges.find(source.state);
                if (found == state_edges.end()) continue;
                for (int at : found->second) {
                    check();
                    const MotionEdge& choice = policy->edges[at];
                    if (choice.move != move.motion || !closure_edge(choice)) continue;
                    base = move.cost; primitive = i; edge = at; break;
                }
            }
            return base;
        }
        double price(int x, int y, int z) const {
            if (reverse_reach) return 0.0;
            Index at = index(x, y, z);
            double historical = history ? history[at]
                : (history_list ? PyFloat_AsDouble(PyList_GET_ITEM(history_list, at)) : 0.0);
            return ((present ? present[at] : 0.0) + historical) * pressure;
        }
        void check(bool force = false) {
            ++checks;
            if ((checks & 4095) == 0 || (force && cancelled)) {
                PyGILState_STATE state = PyGILState_Ensure();
                int interrupted = PyErr_CheckSignals(), stop = 0;
                if (!interrupted && cancelled) {
                    PyObject* result = PyObject_CallNoArgs(cancelled);
                    if (result) {
                        stop = PyObject_IsTrue(result);
                        Py_DECREF(result);
                    } else stop = -1;
                }
                PyGILState_Release(state);
                if (interrupted || stop < 0) throw std::runtime_error("geometric search callback interrupted");
                if (stop) throw Cancelled();
            }
            if (timed && (force || (checks & 63) == 0) && Clock::now() >= deadline) throw Limit();
        }
        void charge() {
            check();
            if (answer.work >= limit) throw Limit();
            ++answer.work;
        }
        static bool same_profile(const Run& a, const Run& b) {
            return a.free == b.free && (!a.free || a.price == b.price);
        }
        bool scan_gap(Row& target, int y, int z, int lo, int hi, int sign,
                      bool stop_at_blocker, int& last) {
            // The caller proves this entire gap is unknown. Each cell is read
            // and charged once, then retained in the query-local sparse profile.
            profile_patch.clear();
            bool blocked = false;
            for (int x = sign > 0 ? lo : hi; lo <= x && x <= hi; x += sign) {
                charge(); ++answer.prepared_cells;
                bool free = flags[index(x, y, z)] != 0;
                Run fresh{x, x, free ? price(x, y, z) : 0.0, free};
                if (!profile_patch.empty() && same_profile(profile_patch.back(), fresh)) {
                    if (sign > 0) profile_patch.back().hi = x;
                    else profile_patch.back().lo = x;
                } else profile_patch.push_back(fresh);
                last = x;
                if (stop_at_blocker && !free) { blocked = true; break; }
            }
            if (sign < 0) std::reverse(profile_patch.begin(), profile_patch.end());
            auto& runs = target.runs;
            auto at = std::lower_bound(runs.begin(), runs.end(), profile_patch.front().lo,
                [](const Run& run, int x) { return run.lo < x; });
            std::size_t first = std::size_t(at - runs.begin()), end = first;
            if (first > 0 && runs[first - 1].hi + 1 == profile_patch.front().lo
                          && same_profile(runs[first - 1], profile_patch.front())) {
                profile_patch.front().lo = runs[--first].lo;
            }
            if (end < runs.size() && profile_patch.back().hi + 1 == runs[end].lo
                                  && same_profile(profile_patch.back(), runs[end])) {
                profile_patch.back().hi = runs[end++].hi;
            }
            runs.erase(runs.begin() + first, runs.begin() + end);
            runs.insert(runs.begin() + first, profile_patch.begin(), profile_patch.end());
            return blocked;
        }
        Row& row(int y, int z, int lo, int hi) {
            Row& result = rows[y * nz + z];
            lo = std::max(0, lo); hi = std::min(nx - 1, hi);
            if (lo > hi) return result;
            auto first = std::lower_bound(result.runs.begin(), result.runs.end(), lo,
                [](const Run& run, int x) { return run.hi < x; });
            if (first != result.runs.end() && first->lo <= lo && first->hi >= hi) return result;
            auto began = Clock::now();
            try {
                int cursor = lo;
                while (cursor <= hi) {
                    check(); ++answer.profile_scans;
                    auto at = std::lower_bound(result.runs.begin(), result.runs.end(), cursor,
                        [](const Run& run, int x) { return run.hi < x; });
                    if (at != result.runs.end() && at->lo <= cursor) {
                        cursor = at->hi + 1;
                        continue;
                    }
                    int last = at == result.runs.end() ? hi : std::min(hi, at->lo - 1);
                    int scanned = cursor;
                    scan_gap(result, y, z, cursor, last, 1, false, scanned);
                    cursor = last + 1;
                }
            } catch (...) {
                answer.preparation_s += std::chrono::duration<double>(Clock::now() - began).count();
                throw;
            }
            answer.preparation_s += std::chrono::duration<double>(Clock::now() - began).count();
            return result;
        }
        void prepare_ray(int y, int z, int origin, int sign, int end) {
            Row& target = rows[y * nz + z];
            auto began = Clock::now();
            try {
                int cursor = origin + sign;
                while (0 <= cursor && cursor < nx && (sign > 0 ? cursor <= end : cursor >= end)) {
                    check(); ++answer.profile_scans;
                    auto at = std::lower_bound(target.runs.begin(), target.runs.end(), cursor,
                        [](const Run& run, int x) { return run.hi < x; });
                    if (at != target.runs.end() && at->lo <= cursor) {
                        if (!at->free) break;
                        cursor = sign > 0 ? at->hi + 1 : at->lo - 1;
                        continue;
                    }
                    int lo = cursor, hi = cursor;
                    if (sign > 0) hi = std::min(end, at == target.runs.end() ? nx - 1 : at->lo - 1);
                    else lo = std::max(end, at == target.runs.begin() ? 0 : (at - 1)->hi + 1);
                    int scanned = cursor;
                    if (scan_gap(target, y, z, lo, hi, sign, true, scanned)) break;
                    cursor = scanned + sign;
                }
            } catch (...) {
                answer.preparation_s += std::chrono::duration<double>(Clock::now() - began).count();
                throw;
            }
            answer.preparation_s += std::chrono::duration<double>(Clock::now() - began).count();
        }
        void measure_distance_prices() {
            // Every primitive and connector must dominate this weighted
            // Manhattan metric. First price XY, then the remaining Z cost.
            auto visit_edges = [&](auto&& visit) {
                for (const auto& level : moves) for (const Move& move : level) {
                    check();
                    visit(std::abs(move.dx) + std::abs(move.dy), std::abs(move.dz), move.cost);
                }
                for (const Extra& edge : extras) {
                    check();
                    int sx, sy, sz, tx, ty, tz;
                    cell(edge.source, sx, sy, sz); cell(edge.target, tx, ty, tz);
                    visit(std::abs(tx - sx) + std::abs(ty - sy), std::abs(tz - sz), edge.cost);
                }
            };
            xy_price = infinity;
            distance_is_linear = nz == 1;
            visit_edges([&](int xy, int, double cost) {
                if (xy == 0) return;
                if (xy > 1) distance_is_linear = false;
                long double unit = static_cast<long double>(cost) / xy;
                while (unit > 0.0 && unit * xy > cost) unit = std::nextafter(unit, 0.0L);
                xy_price = std::min(xy_price, unit);
            });
            if (!std::isfinite(xy_price)) xy_price = 0.0;
            z_price = infinity;
            visit_edges([&](int xy, int z, double cost) {
                if (z == 0) return;
                long double unit = std::max(0.0L, (cost - xy_price * xy) / z);
                while (unit > 0.0 && xy_price * xy + unit * z > cost)
                    unit = std::nextafter(unit, 0.0L);
                z_price = std::min(z_price, unit);
            });
            if (!std::isfinite(z_price)) z_price = 0.0;
        }
        void prepare_distance_topology() {
            if (!distance_topology.empty()) return;
            distance_topology.resize(nz);
            for (int z = 0; z < nz; ++z) for (const Move& move : moves[z]) {
                check();
                if (0 <= z + move.dz && z + move.dz < nz)
                    distance_topology[z].push_back({std::abs(move.dx) + std::abs(move.dy),
                                                   0, move.dz, false, move.cost});
            }
            for (const Extra& edge : extras) {
                check();
                int sx, sy, sz, tx, ty, tz;
                cell(edge.source, sx, sy, sz); cell(edge.target, tx, ty, tz);
                distance_topology[sz].push_back({std::abs(tx - sx) + std::abs(ty - sy),
                                               0, tz - sz, false, edge.cost});
            }
            for (auto& level : distance_topology) {
                std::sort(level.begin(), level.end(), [&](const Move& a, const Move& b) {
                    check();
                    if (a.dz != b.dz) return a.dz < b.dz;
                    if (a.dx != b.dx) return a.dx < b.dx;
                    return a.cost < b.cost;
                });
                level.erase(std::unique(level.begin(), level.end(), [&](const Move& a, const Move& b) {
                    check(); return a.dz == b.dz && a.dx == b.dx;
                }), level.end());
            }
        }
        std::shared_ptr<const DistanceProfile> prepare_distance_profile(int goal_level) {
            int extent = nx + ny - 2;
            prepare_distance_topology();
            {
                std::lock_guard<std::mutex> lock(distance_cache_mutex);
                for (auto at = distance_cache.rbegin(); at != distance_cache.rend(); ++at)
                    if ((*at)->matches(distance_topology, extent, goal_level)) return *at;
            }
            auto profile = std::make_shared<DistanceProfile>();
            profile->extent = extent; profile->goal_level = goal_level;
            profile->topology = distance_topology;
            profile->costs.resize(std::size_t(extent + 1) * nz, infinity);
            profile->corners.resize(nz);
            profile->monotone_from.resize(nz, 0);
            std::vector<std::vector<Move>> incoming(nz);
            for (int z = 0; z < nz; ++z) for (const Move& move : distance_topology[z]) {
                check();
                incoming[z + move.dz].push_back({move.dx, 0, z, false, move.cost});
            }
            // This is a topology relaxation, not a coordinate-space search.
            // A displacement k can change Manhattan distance d only to
            // |d-k|..d+k with the same parity as d+k. Retaining that overshoot
            // obligation avoids treating a ramp as a free XY return at d=0.
            std::priority_queue<Entry, std::vector<Entry>, Later> pending;
            profile->costs[goal_level] = 0.0;
            pending.push({0.0, 0, goal_level});
            while (!pending.empty()) {
                check();
                Entry entry = pending.top(); pending.pop();
                if (entry.lower != profile->costs[entry.label]) continue;
                int distance = int(entry.label / nz), level = int(entry.label % nz);
                for (const Move& edge : incoming[level]) {
                    int first = std::abs(distance - edge.dx), last = std::min(extent, distance + edge.dx);
                    for (int d = first; d <= last; d += 2) {
                        check();
                        Index at = Index(d) * nz + edge.dz;
                        long double value = entry.lower + edge.cost;
                        if (value < profile->costs[at]) {
                            profile->costs[at] = value;
                            pending.push({value, 0, at});
                        }
                    }
                }
            }
            for (int z = 0; z < nz; ++z) {
                for (int d = 0; d <= extent; ++d) {
                    check();
                    long double& value = profile->costs[std::size_t(d) * nz + z];
                    // Keep queue bounds finite for unreachable projected
                    // states: exact reachable-row certificates still require
                    // exhaustive exploration of the original admitted graph.
                    if (!std::isfinite(value)) value = xy_price * d + z_price * std::abs(z - goal_level);
                }
                for (int d = 1; d <= extent; ++d) {
                    check();
                    if (profile->costs[std::size_t(d) * nz + z]
                        < profile->costs[std::size_t(d - 1) * nz + z])
                        profile->monotone_from[z] = d;
                }
                for (int d = 1; d < extent; ++d) {
                    check();
                    long double before = profile->costs[std::size_t(d - 1) * nz + z];
                    long double value = profile->costs[std::size_t(d) * nz + z];
                    long double after = profile->costs[std::size_t(d + 1) * nz + z];
                    if (after - value > value - before) profile->corners[z].push_back(d);
                }
            }
            check(true);
            {
                std::lock_guard<std::mutex> lock(distance_cache_mutex);
                if (distance_cache.size() == 16) distance_cache.erase(distance_cache.begin());
                distance_cache.push_back(profile);
            }
            return profile;
        }
        void prepare_goal_segments() {
            for (const Goal& goal : goals) {
                check();
                if (std::isfinite(goal.toll)) continue;
                // Preserve the original individual-goal path for nonfinite
                // prices; they must never enter an ordering comparator.
                for (const Goal& single : goals) {
                    check();
                    goal_segments.push_back({single.x, single.x, single.y, single.y, single.z, single.toll});
                }
                return;
            }
            auto sorted = goals;
            std::sort(sorted.begin(), sorted.end(), [this](const Goal& a, const Goal& b) {
                check();
                if (a.z != b.z) return a.z < b.z;
                if (a.y != b.y) return a.y < b.y;
                if (a.toll != b.toll) return a.toll < b.toll;
                return a.x < b.x;
            });
            for (const Goal& goal : sorted) {
                check();
                if (!goal_segments.empty()) {
                    auto& last = goal_segments.back();
                    if (last.z == goal.z && last.y == goal.y && last.toll == goal.toll
                        && goal.x <= last.hi + 1) {
                        last.hi = std::max(last.hi, goal.x);
                        continue;
                    }
                }
                goal_segments.push_back({goal.x, goal.x, goal.y, goal.y, goal.z, goal.toll});
            }
            // Adjacent rows with identical X runs form an exact rectangle:
            // no hole, level change or unequal terminal toll is filled.
            std::sort(goal_segments.begin(), goal_segments.end(), [this](const GoalSegment& a, const GoalSegment& b) {
                check();
                if (a.z != b.z) return a.z < b.z;
                if (a.lo != b.lo) return a.lo < b.lo;
                if (a.hi != b.hi) return a.hi < b.hi;
                if (a.toll != b.toll) return a.toll < b.toll;
                return a.y < b.y;
            });
            std::size_t count = 0;
            for (const GoalSegment& goal : goal_segments) {
                check();
                if (count) {
                    auto& last = goal_segments[count - 1];
                    if (last.z == goal.z && last.lo == goal.lo && last.hi == goal.hi
                        && last.toll == goal.toll && goal.y <= last.y_hi + 1) {
                        last.y_hi = std::max(last.y_hi, goal.y_hi);
                        continue;
                    }
                }
                goal_segments[count++] = goal;
            }
            goal_segments.resize(count);
        }
        long double lower(const Label& label) {
            if (reverse_reach) return 0.0;
            if (goals.empty()) return std::min(label.cost, label.value(label.hi));
            long double result = infinity;
            for (const GoalSegment& goal : goal_segments) {
                check();
                const DistanceProfile* profile = nullptr;
                if (!distance_is_linear) {
                    if (distance_profiles.empty()) distance_profiles.resize(nz);
                    auto& cached = distance_profiles[goal.z];
                    if (!cached) cached = prepare_distance_profile(goal.z);
                    profile = cached.get();
                }
                int y_distance = std::max({0, goal.y - label.y, label.y - goal.y_hi});
                auto visit = [&](int lo, int hi, int y_distance) {
                    auto distance_x = [&](int x) { return x < lo ? lo - x : x > hi ? x - hi : 0; };
                    auto consider = [&](int x) {
                        int d = distance_x(x) + y_distance;
                        long double distance = profile ? profile->costs[std::size_t(d) * nz + label.z]
                            : xy_price * d + z_price * std::abs(label.z - goal.z);
                        long double value = label.value(x) + distance;
                        bool at_goal = lo <= x && x <= hi && y_distance == 0 && label.z == goal.z;
                        if (backwards) {
                            if (!at_goal) value += label.toll;
                            if (charge_occupied_cells) value += goal.toll;
                        } else if (!at_goal) value += goal.toll;
                        result = std::min(result, value);
                    };
                    // A point interval has one candidate, including at every
                    // profile kink. Re-evaluating it cannot improve the bound.
                    consider(label.lo);
                    if (label.lo == label.hi) return;
                    int near_lo = std::clamp(lo, label.lo, label.hi);
                    int near_hi = std::clamp(hi, label.lo, label.hi);
                    if (label.lo < near_lo && near_lo < label.hi) consider(near_lo);
                    consider(label.hi);
                    if (near_hi != near_lo && label.lo < near_hi && near_hi < label.hi)
                        consider(near_hi);
                    if (profile) {
                        const auto& corners = profile->corners[label.z];
                        int first = std::min(distance_x(near_lo), distance_x(near_hi)) + y_distance;
                        int last = std::max(distance_x(label.lo), distance_x(label.hi)) + y_distance;
                        for (auto at = std::lower_bound(corners.begin(), corners.end(), first);
                             at != corners.end() && *at <= last; ++at) {
                            check();
                            int delta = *at - y_distance;
                            int left = lo - delta, right = hi + delta;
                            if (label.lo < left && left < label.hi && left != near_lo && left != near_hi)
                                consider(left);
                            if (label.lo < right && right < label.hi && right != near_lo && right != near_hi
                                && right != left) consider(right);
                        }
                    }
                };
                // A directed profile may decrease near the goal but be
                // monotone over every distance this label can reach. Only
                // those suffixes admit the nearest-point goal-run minimum.
                int nearest = std::max({0, goal.lo - label.hi, label.lo - goal.hi}) + y_distance;
                if (profile && nearest < profile->monotone_from[label.z])
                    for (int y = goal.y; y <= goal.y_hi; ++y) {
                        int distance = std::abs(label.y - y);
                        for (int x = goal.lo; x <= goal.hi; ++x) { check(); visit(x, x, distance); }
                    }
                // A negative entry toll can favor a different goal over the
                // zero-entry goal itself. Keep the original row minima there.
                else if (nearest == 0 && goal.y != goal.y_hi && (backwards ? label.toll : goal.toll) < 0)
                    for (int y = goal.y; y <= goal.y_hi; ++y) { check(); visit(goal.lo, goal.hi, std::abs(label.y - y)); }
                else visit(goal.lo, goal.hi, y_distance);
            }
            return result;
        }
        int proximity(const Label& label) {
            if (reverse_reach) return 0;
            int result = nx + ny + nz;
            for (const GoalSegment& goal : goal_segments) {
                check();
                result = std::min(result, std::max({0, goal.lo - label.hi, label.lo - goal.hi})
                    + std::max({0, goal.y - label.y, label.y - goal.y_hi}) + std::abs(label.z - goal.z));
            }
            return result;
        }
        bool is_goal(int x, int y, int z) {
            for (int goal : rows[y * nz + z].goals) {
                check();
                if (goal == x) return true;
            }
            return false;
        }
        void queue_horizontal(Index identity, long double bound) {
            Label source = labels[identity];
            for (int sign : {-1, 1}) {
                int primitive = -1, edge = -1;
                long double base = horizontal_move(source, sign, primitive, edge);
                if (!std::isfinite(base)) continue;
                int origin = sign < 0 ? (source.slope < -base ? source.hi : source.lo)
                                      : (source.slope > base ? source.lo : source.hi);
                // Ignore unknown future congestion while retaining the original
                // interval bound. Each direction remains a queued continuation.
                Label ray = source;
                ray.lo = sign < 0 ? 0 : origin + 1;
                ray.hi = sign < 0 ? origin - 1 : nx - 1;
                if (ray.lo > ray.hi) continue;
                ray.cost = source.value(origin) + base * std::abs(ray.lo - origin);
                ray.slope = sign * base;
                long double pending = std::max(bound, lower(ray));
                if (pending < best) queue.push({pending, proximity(source), identity, sign});
            }
        }
        bool improved(const Label& label, int& lo, int& hi, Index old) {
            if (old < 0) return true;
            const Label& previous = labels[old];
            bool left = label.value(lo) < previous.value(lo);
            bool right = label.value(hi) < previous.value(hi);
            if (left == right) return left;
            int a = lo, b = hi;
            while (a < b) {
                check();
                int middle = a + (b - a) / 2;
                if ((label.value(middle) < previous.value(middle)) == left) a = middle + 1;
                else b = middle;
            }
            if (left) hi = a - 1; else lo = a;
            return true;
        }
        void append(int lo, int hi, Index label) {
            if (!patch.empty() && patch.back().label == label && patch.back().hi + 1 == lo)
                patch.back().hi = hi;
            else patch.push_back({lo, hi, label});
        }
        bool offer(const Label& label) {
            check(); ++answer.offers;
            if (std::isfinite(best) && lower(label) >= best) return false;
            auto& envelope = this->envelope(label.y, label.z, label.state);
            if (envelope.empty()) envelope.push_back({0, nx - 1, -1});
            auto begin = std::lower_bound(envelope.begin(), envelope.end(), label.lo,
                [](const Segment& segment, int x) { return segment.hi < x; });
            std::size_t first = std::size_t(begin - envelope.begin()), end = first;
            patch.clear();
            bool changed = false;
            for (; end < envelope.size() && envelope[end].lo <= label.hi; ++end) {
                check(); ++answer.profile_scans;
                const Segment& segment = envelope[end];
                int a = segment.lo, b = segment.hi;
                Index old = segment.label;
                if (a < label.lo) append(a, label.lo - 1, old);
                int lo = std::max(a, label.lo), hi = std::min(b, label.hi);
                int l = lo, h = hi;
                if (!improved(label, l, h, old)) append(lo, hi, old);
                else {
                    changed = true;
                    if (lo < l) append(lo, l - 1, old);
                    Label fresh = label; fresh.lo = l; fresh.hi = h; fresh.cost = label.value(l);
                    if (backwards) fresh.toll = price(l, fresh.y, fresh.z);
                    Index identity = Index(labels.size()); labels.push_back(fresh);
                    append(l, h, identity);
                    queue.push({lower(fresh), proximity(fresh), identity});
                    auto terminal = [&](int x, int action) {
                        check();
                        long double cost = fresh.value(x)
                            + (backwards && charge_occupied_cells ? price(x, fresh.y, fresh.z) : 0.0);
                        if (l <= x && x <= h && cost < best) {
                            best = cost; goal_label = identity; goal_x = x; answer.final_action = action;
                        }
                    };
                    if (policy) {
                        for (const TerminalGoal& goal : state_rows[fresh.y * nz + fresh.z][fresh.state].goals)
                            terminal(goal.x, goal.action);
                    } else for (int x : rows[fresh.y * nz + fresh.z].goals) terminal(x, -1);
                    if (h < hi) append(h + 1, hi, old);
                }
                if (b > label.hi) append(label.hi + 1, b, old);
            }
            if (!changed) return false;
            if (first > 0 && envelope[first - 1].label == patch.front().label) {
                patch.front().lo = envelope[--first].lo;
            }
            if (end < envelope.size() && envelope[end].label == patch.back().label) {
                patch.back().hi = envelope[end++].hi;
            }
            envelope.erase(envelope.begin() + first, envelope.begin() + end);
            envelope.insert(envelope.begin() + first, patch.begin(), patch.end());
            return true;
        }
        void cross(Index identity, int lo, int hi, const Move& move, int edge = -1, int primitive = -1) {
            // Copy: offer may grow labels and invalidate references into it.
            Label source = labels[identity];
            int state = policy ? policy->edges[edge].target : source.state;
            int y = source.y + move.dy, z = source.z + move.dz;
            if (y < 0 || y >= ny || z < 0 || z >= nz) return;
            int target_lo = std::max(0, lo + move.dx), target_hi = std::min(nx - 1, hi + move.dx);
            if (target_lo > target_hi) return;
            if (std::isfinite(best) && lower({y, z, target_lo, target_hi,
                    source.value(target_lo - move.dx) + move.cost + (backwards ? source.toll : 0.0), source.slope,
                    identity, -1, move.dx, move.via, false}) >= best) return;
            row(y, z, target_lo, target_hi);
            int via_z = reverse_reach || backwards ? z : source.z;
            bool eager_mid = reverse_reach || backwards || (move.dy == 0 && z == source.z);
            if (move.via && eager_mid) row(source.y + move.dy / 2, via_z,
                                           target_lo - move.dx / 2, target_hi - move.dx / 2);
            const auto& targets = rows[y * nz + z].runs;
            const std::vector<Run>* mids = move.via
                ? &rows[(source.y + move.dy / 2) * nz + via_z].runs : nullptr;
            // Explicit starts may be released/blocked, but are admitted as
            // sources. They terminate the reverse probe, never become free
            // intermediate or ramp-via cells.
            if (reverse_reach) for (int x : rows[y * nz + z].goals) {
                check();
                if (target_lo <= x && x <= target_hi &&
                    (!move.via || flags[index(x - move.dx / 2, source.y + move.dy / 2, via_z)]))
                    best = 0.0;
            }
            if (backwards) for (int x : rows[y * nz + z].goals) {
                check();
                if (x < target_lo || x > target_hi || flags[index(x, y, z)]) continue;
                int mx = x - move.dx / 2, my = source.y + move.dy / 2;
                if (move.via && !flags[index(mx, my, via_z)]) continue;
                offer({y, z, x, x, source.value(x - move.dx) + move.cost + source.toll
                       + (move.via && charge_occupied_cells ? price(mx, my, via_z) : 0.0),
                       0, identity, -1, move.dx, move.via, false});
            }
            auto first_target = std::lower_bound(targets.begin(), targets.end(), lo + move.dx,
                [](const Run& run, int x) { return run.hi < x; });
            for (auto it = first_target; it != targets.end() && it->lo <= hi + move.dx; ++it) {
                const Run& target = *it;
                check(); ++answer.profile_scans;
                if (!target.free) continue;
                int l = std::max(lo + move.dx, target.lo);
                int h = std::min(hi + move.dx, target.hi);
                if (l > h) continue;
                if (!mids) {
                    ++answer.intersections;
                    offer({y, z, l, h, source.value(l - move.dx) + move.cost + (backwards ? source.toll : target.price),
                           source.slope, identity, -1, move.dx, move.via, false, 0.0, state, edge, primitive});
                } else {
                    // A blocked landing cannot use a ramp: leave its via
                    // profile unknown rather than scanning it needlessly.
                    if (!eager_mid) row(source.y + move.dy / 2, via_z,
                                        l - move.dx / 2, h - move.dx / 2);
                    auto first_mid = std::lower_bound(mids->begin(), mids->end(), l - move.dx / 2,
                        [](const Run& run, int x) { return run.hi < x; });
                    for (auto mi = first_mid; mi != mids->end() && mi->lo <= h - move.dx / 2; ++mi) {
                        const Run& mid = *mi;
                        check(); ++answer.profile_scans;
                        if (!mid.free) continue;
                        int ml = std::max(l, mid.lo + move.dx / 2);
                        int mh = std::min(h, mid.hi + move.dx / 2);
                        if (ml > mh) continue;
                        ++answer.intersections;
                        offer({y, z, ml, mh, source.value(ml - move.dx) + move.cost + (backwards ? source.toll : target.price)
                               + (charge_occupied_cells ? mid.price : 0.0),
                               source.slope, identity, -1, move.dx, true, false, 0.0, state, edge, primitive});
                    }
                }
            }
        }
        void cross_motion(Index identity, int lo, int hi, const Move& move, int edge, int primitive) {
            const MotionEdge& choice = policy->edges[edge];
            if (choice.guard < 0) { cross(identity, lo, hi, move, edge, primitive); return; }
            Label source = labels[identity];
            for (const MotionBox& box : policy->guards[choice.guard]) {
                check();
                if (source.y < box.ylo || source.y > box.yhi || source.z < box.zlo || source.z > box.zhi) continue;
                int l = std::max(lo, box.xlo), h = std::min(hi, box.xhi);
                if (l <= h) cross(identity, l, h, move, edge, primitive);
            }
        }
        void horizontal(Index identity, int lo, int hi, int direction) {
            Label source = labels[identity];
            int left_primitive = -1, right_primitive = -1, left_edge = -1, right_edge = -1;
            double left_base = horizontal_move(source, -1, left_primitive, left_edge);
            double right_base = horizontal_move(source, 1, right_primitive, right_edge);
            if (!std::isfinite(left_base) && !std::isfinite(right_base)) return;
            row(source.y, source.z, lo, hi);
            double toll = price(lo, source.y, source.z);
            for (int sign : {-1, 1}) {
                if (direction != 0 && sign != direction) continue;
                long double base = sign < 0 ? left_base : right_base;
                int primitive = sign < 0 ? left_primitive : right_primitive;
                int edge = sign < 0 ? left_edge : right_edge;
                if (!std::isfinite(base)) continue;
                int origin = sign < 0 ? (source.slope < -(base + toll) ? hi : lo)
                                      : (source.slope > base + toll ? lo : hi);
                int end = sign < 0 ? 0 : nx - 1;
                // Offer at a goal projection before inspecting the boundary
                // tail: that offer may establish a closing incumbent.
                if (!reverse_reach) for (const GoalSegment& goal : goal_segments) {
                    check();
                    for (int x : {goal.lo, goal.hi})
                        if (sign > 0 ? x > origin && x < end : x < origin && x > end) end = x;
                }
                long double cost = source.value(origin);
                double previous_toll = toll;
                int at = origin;
                for (;;) {
                    prepare_ray(source.y, source.z, at, sign, end);
                    const auto& runs = rows[source.y * nz + source.z].runs;
                    if (reverse_reach) for (const Run& run : runs) {
                        check();
                        if (!run.free || run.lo > origin || run.hi < origin) continue;
                        for (int x : rows[source.y * nz + source.z].goals) {
                            check();
                            if (x == (sign < 0 ? run.lo - 1 : run.hi + 1)) best = 0.0;
                        }
                        break;
                    }
                    for (int n = 0; n < int(runs.size()); ++n) {
                        check(); ++answer.profile_scans;
                        const Run& run = runs[sign > 0 ? n : int(runs.size()) - n - 1];
                        if (sign > 0) {
                            if (run.hi <= at) continue;
                            if (!run.free) break;
                            int l = std::max(run.lo, at + 1), h = std::min(run.hi, end);
                            if (l != at + 1 || l > h) break;
                            long double slope = base + run.price;
                            long double first = cost + base + (backwards ? previous_toll : run.price);
                            if (!offer({source.y, source.z, l, h, first, slope, identity, origin, 0, false, true,
                                        0.0, source.state, edge, primitive})) break;
                            cost = first + slope * (h - l); at = h; previous_toll = run.price;
                        } else {
                            if (run.lo >= at) continue;
                            if (!run.free) break;
                            int l = std::max(run.lo, end), h = std::min(run.hi, at - 1);
                            if (h != at - 1 || l > h) break;
                            long double slope = -(base + run.price);
                            long double first = cost + base + (backwards ? previous_toll : run.price)
                                - slope * (at - l - 1);
                            if (!offer({source.y, source.z, l, h, first, slope, identity, origin, 0, false, true,
                                        0.0, source.state, edge, primitive})) break;
                            cost = first; at = l; previous_toll = run.price;
                        }
                    }
                    int next = at + sign;
                    if (backwards && (sign > 0 ? next <= end : next >= end)
                        && !flags[index(next, source.y, source.z)] && is_goal(next, source.y, source.z))
                        offer({source.y, source.z, next, next, cost + base + previous_toll,
                               0, identity, origin, 0, false, true});
                    if (lower(source) >= best) return;
                    int boundary = sign < 0 ? 0 : nx - 1;
                    if (end == boundary || at != end) break;
                    end = boundary;
                }
            }
        }
        void reconstruct() {
            Index identity = goal_label;
            int x = goal_x;
            if (policy) answer.final_state = labels[goal_label].state;
            while (identity >= 0) {
                check();
                const Label& label = labels[identity];
                answer.path.push_back(index(x, label.y, label.z));
                if (label.parent < 0) {
                    if (policy) answer.initial_state = label.state;
                    break;
                }
                const Label& parent = labels[label.parent];
                if (policy) answer.motion.push_back({Index(answer.path.size() - 1),
                    label.edge, label.primitive, parent.state, label.state, label.via ? 2 : 1});
                if (label.fixed >= 0) {
                    int target = label.fixed;
                    if (label.horizontal) {
                        int step = target > x ? 1 : -1;
                        for (int k = x + step; k != target; k += step) {
                            check(); answer.path.push_back(index(k, label.y, label.z));
                            if (policy) answer.motion.push_back({Index(answer.path.size() - 1),
                                label.edge, label.primitive, parent.state, label.state, 1});
                        }
                    }
                    x = target;
                } else {
                    int prior = x - label.dx;
                    if (label.via) answer.path.push_back(index(prior + label.dx / 2,
                                                              (parent.y + label.y) / 2, backwards ? label.z : parent.z));
                    x = prior;
                }
                identity = label.parent;
            }
            if (!backwards) std::reverse(answer.path.begin(), answer.path.end());
            if (policy) {
                std::reverse(answer.motion.begin(), answer.motion.end());
                for (MotionTrace& trace : answer.motion) {
                    check();
                    // Reverse offsets identify landings, not ramp-via cells.
                    trace.path_index = Index(answer.path.size() - 1) - trace.path_index - trace.width;
                }
            }
        }
        void certify_motion() {
            auto began = Clock::now();
            try {
                reconstruct();
                const auto& path = answer.path;
                auto endpoint = [&](const auto& endpoints, Index cell, int state, int action) {
                    for (const MotionEndpoint& point : endpoints) {
                        check();
                        if (point.cell == cell && point.state == state && point.action == action) return true;
                    }
                    return false;
                };
                if (path.empty() || !endpoint(policy->initial, path.front(), answer.initial_state, -1)
                    || !endpoint(policy->accepting, path.back(), answer.final_state, answer.final_action))
                    throw std::logic_error("motion witness has no admitted endpoint state");
                int x, y, z; cell(path.front(), x, y, z);
                double cost = charge_occupied_cells ? price(x, y, z) : 0.0;
                int state = answer.initial_state;
                std::size_t at = 0;
                for (const MotionTrace& trace : answer.motion) {
                    check(); ++answer.certified_edges;
                    if (trace.path_index != Index(at) || trace.edge < 0 || trace.edge >= int(policy->edges.size()))
                        throw std::logic_error("motion witness has invalid primitive provenance");
                    const MotionEdge& edge = policy->edges[trace.edge];
                    if (edge.source != state || trace.source != state || trace.target != edge.target
                        || trace.primitive < 0 || trace.primitive >= int(forward_moves[z].size()))
                        throw std::logic_error("motion witness has invalid state transition");
                    const Move& move = forward_moves[z][trace.primitive];
                    if (move.motion != edge.move)
                        throw std::logic_error("motion witness substituted a physical primitive");
                    if (edge.guard >= 0) {
                        bool allowed = false;
                        for (const MotionBox& box : policy->guards[edge.guard]) {
                            check();
                            if (box.xlo <= x && x <= box.xhi && box.ylo <= y && y <= box.yhi
                                && box.zlo <= z && z <= box.zhi) { allowed = true; break; }
                        }
                        if (!allowed) throw std::logic_error("motion witness violates its source guard");
                    }
                    int tx = x + move.dx, ty = y + move.dy, tz = z + move.dz;
                    std::size_t end = at + (move.via ? 2 : 1);
                    if (tx < 0 || tx >= nx || ty < 0 || ty >= ny || tz < 0 || tz >= nz
                        || end >= path.size() || path[end] != index(tx, ty, tz) || !flags[path[end]])
                        throw std::logic_error("motion witness violates its directed landing");
                    double via_price = 0.0;
                    if (move.via) {
                        int mx = x + move.dx / 2, my = y + move.dy / 2;
                        Index via = index(mx, my, z);
                        if (path[at + 1] != via || !flags[via])
                            throw std::logic_error("motion witness violates its source-level ramp via");
                        if (charge_occupied_cells) via_price = price(mx, my, z);
                    }
                    cost += move.cost + price(tx, ty, tz) + via_price;
                    x = tx; y = ty; z = tz; state = edge.target; at = end;
                }
                if (at + 1 != path.size() || state != answer.final_state || !std::isfinite(cost)
                    || std::abs(cost - best) > 1e-9 * std::max(1.0L, std::abs(best)))
                    throw std::logic_error("motion witness differs from its directed graph price");
                answer.cost = cost;
                check(true);
            } catch (...) {
                answer.certification_s = std::chrono::duration<double>(Clock::now() - began).count();
                throw;
            }
            answer.certification_s = std::chrono::duration<double>(Clock::now() - began).count();
        }
        void certify() {
            if (policy) { certify_motion(); return; }
            auto began = Clock::now();
            try {
                reconstruct();
                const auto& path = answer.path;
                const auto& admitted_starts = backwards ? goal_indices : starts;
                if (path.empty() || std::find(admitted_starts.begin(), admitted_starts.end(), path.front()) == admitted_starts.end())
                    throw std::logic_error("interval witness has no admitted start");
                int sx, sy, sz; cell(path.front(), sx, sy, sz);
                std::vector<double> costs(path.size(), infinity);
                costs[0] = charge_occupied_cells ? price(sx, sy, sz) : 0.0;
                for (std::size_t at = 0; at + 1 < path.size(); ++at) {
                    check();
                    if (!std::isfinite(costs[at])) continue;
                    int x, y, z; cell(path[at], x, y, z);
                    for (const Move& move : forward_moves[z]) {
                        check(); ++answer.certified_edges;
                        int tx = x + move.dx, ty = y + move.dy, tz = z + move.dz;
                        if (tx < 0 || tx >= nx || ty < 0 || ty >= ny || tz < 0 || tz >= nz) continue;
                        Index target = index(tx, ty, tz);
                        std::size_t end = at + (move.via ? 2 : 1);
                        if (end >= path.size() || path[end] != target || !flags[target]) continue;
                        double via_price = 0.0;
                        if (move.via) {
                            Index via = index(x + move.dx / 2, y + move.dy / 2, z);
                            if (path[at + 1] != via || !flags[via]) continue;
                            if (charge_occupied_cells) via_price = price(x + move.dx / 2, y + move.dy / 2, z);
                        }
                        costs[end] = std::min(costs[end], costs[at] + move.cost + price(tx, ty, tz) + via_price);
                    }
                    int tx, ty, tz; cell(path[at + 1], tx, ty, tz);
                    const auto& links = rows[(backwards ? ty : y) * nz + (backwards ? tz : z)].extras;
                    for (const Extra& edge : links) {
                        check(); ++answer.certified_edges;
                        Index source = backwards ? edge.target : edge.source;
                        Index target = backwards ? edge.source : edge.target;
                        if (source != path[at] || target != path[at + 1] || !flags[target]) continue;
                        costs[at + 1] = std::min(costs[at + 1], costs[at] + edge.cost + price(tx, ty, tz));
                    }
                }
                bool goal = false;
                for (Index end : backwards ? starts : goal_indices) {
                    check();
                    if (end == path.back()) goal = true;
                }
                if (!goal || !std::isfinite(costs.back()) || std::abs(costs.back() - best) > 1e-9 * std::max(1.0L, std::abs(best)))
                    throw std::logic_error("interval witness differs from its directed graph price");
                answer.cost = costs.back();
                check(true);
            } catch (...) {
                answer.certification_s = std::chrono::duration<double>(Clock::now() - began).count();
                throw;
            }
            answer.certification_s = std::chrono::duration<double>(Clock::now() - began).count();
        }
        void memory() {
            answer.labels = Index(labels.size());
            answer.memory_bytes = Index(labels.capacity() * sizeof(Label) + rows.capacity() * sizeof(Row)
                                         + goals.capacity() * sizeof(Goal) + queue.size() * sizeof(Entry)
                                         + goal_segments.capacity() * sizeof(GoalSegment)
                                         + patch.capacity() * sizeof(Segment)
                                         + profile_patch.capacity() * sizeof(Run));
            for (const Row& row : rows) answer.memory_bytes += Index(row.runs.capacity() * sizeof(Run)
                + row.envelope.capacity() * sizeof(Segment) + row.extras.capacity() * sizeof(Extra)
                + row.goals.capacity() * sizeof(int));
            if (policy) {
                answer.memory_bytes += Index(sizeof(Policy) + state_rows.capacity() * sizeof(StateRows)
                    + state_edges.bucket_count() * sizeof(void*)
                    + answer.motion.capacity() * sizeof(MotionTrace)
                    + policy->edges.capacity() * sizeof(MotionEdge)
                    + policy->guards.capacity() * sizeof(std::vector<MotionBox>)
                    + (policy->initial.capacity() + policy->accepting.capacity()) * sizeof(MotionEndpoint));
                for (const auto& row : state_rows) {
                    answer.memory_bytes += Index(row.bucket_count() * sizeof(void*) + row.size() * sizeof(StateRows::value_type));
                    for (const auto& item : row) answer.memory_bytes += Index(
                        item.second.envelope.capacity() * sizeof(Segment) + item.second.goals.capacity() * sizeof(TerminalGoal));
                }
                for (const auto& item : state_edges) answer.memory_bytes += Index(
                    sizeof(decltype(state_edges)::value_type) + item.second.capacity() * sizeof(int));
                for (const auto& guard : policy->guards) answer.memory_bytes += Index(guard.capacity() * sizeof(MotionBox));
                answer.memory_bytes += Index(forward_moves.capacity() * sizeof(std::vector<Move>));
                for (const auto& level : forward_moves) answer.memory_bytes += Index(level.capacity() * sizeof(Move));
            }
            answer.memory_bytes += Index(distance_profiles.capacity() * sizeof(std::shared_ptr<const DistanceProfile>));
            answer.memory_bytes += Index(distance_topology.capacity() * sizeof(std::vector<Move>));
            for (const auto& level : distance_topology)
                answer.memory_bytes += Index(level.capacity() * sizeof(Move));
            for (const auto& profile : distance_profiles) if (profile) {
                answer.memory_bytes += Index(profile->costs.capacity() * sizeof(long double)
                    + profile->corners.capacity() * sizeof(std::vector<int>)
                    + profile->monotone_from.capacity() * sizeof(int)
                    + profile->topology.capacity() * sizeof(std::vector<Move>));
                for (const auto& corners : profile->corners)
                    answer.memory_bytes += Index(corners.capacity() * sizeof(int));
                for (const auto& level : profile->topology)
                    answer.memory_bytes += Index(level.capacity() * sizeof(Move));
            }
            answer.memory_bytes += Index(answer.path.capacity() * sizeof(Index) + answer.reached.capacity() * sizeof(Reach));
            answer.memory_bytes += Index(backward_moves.capacity() * sizeof(std::vector<Move>)
                + backward_extras.capacity() * sizeof(Extra));
            for (const auto& level : backward_moves)
                answer.memory_bytes += Index(level.capacity() * sizeof(Move));
        }
    public:
        Wave(int nx_, int ny_, int nz_, const unsigned char* flags_, const double* history_, PyObject* history_list_,
             double pressure_, const std::vector<std::vector<Move>>& moves_, const std::vector<Index>& starts_,
             const std::vector<Index>& goals_, const std::vector<Extra>& extras_, Index limit_, double remaining,
             const double* present_, bool charge_occupied_cells_, PyObject* cancelled_, bool reverse_reach_,
             bool backwards_, bool transposed_, const Policy* policy_ = nullptr)
            : nx(transposed_ ? ny_ : nx_), ny(transposed_ ? nx_ : ny_), nz(nz_), transposed(transposed_),
              flags(flags_), history(history_), history_list(history_list_),
              present(present_), charge_occupied_cells(charge_occupied_cells_), cancelled(cancelled_),
              reverse_reach(reverse_reach_), backwards(backwards_), pressure(pressure_),
              forward_moves(moves_), forward_extras(extras_), moves(backwards_ ? backward_moves : moves_),
              starts(starts_), goal_indices(goals_), extras(backwards_ ? backward_extras : extras_),
              policy(policy_), limit(limit_), deadline(Clock::now()), timed(remaining >= 0),
              rows(std::size_t(ny) * nz) {
            if (!policy) deadline = Clock::now();
            deadline += std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(std::max(0.0, remaining)));
        }
        Answer solve() {
            auto began = Clock::now();
            try {
                check(true);
                if (limit <= 0) throw Limit();
                for (Index goal : goal_indices) {
                    check(); int x, y, z; cell(goal, x, y, z);
                    goals.push_back({x, y, z, price(x, y, z)}); rows[y * nz + z].goals.push_back(x);
                }
                if (policy) {
                    auto prepared = Clock::now();
                    try {
                        state_rows.resize(rows.size());
                        for (int i = 0; i < int(policy->edges.size()); ++i) {
                            charge(); state_edges[policy->edges[i].source].push_back(i);
                        }
                        for (const MotionEndpoint& goal : policy->accepting) {
                            charge(); int x, y, z; cell(goal.cell, x, y, z);
                            state_rows[y * nz + z][goal.state].goals.push_back({x, goal.action});
                        }
                    } catch (...) {
                        answer.preparation_s += std::chrono::duration<double>(Clock::now() - prepared).count();
                        throw;
                    }
                    answer.preparation_s += std::chrono::duration<double>(Clock::now() - prepared).count();
                }
                if (backwards) {
                    backward_moves.resize(nz);
                    for (int z = 0; z < nz; ++z) for (const Move& move : forward_moves[z]) {
                        check();
                        int target = z + move.dz;
                        if (target >= 0 && target < nz)
                            backward_moves[target].push_back({-move.dx, -move.dy, -move.dz, move.via, move.cost});
                    }
                    backward_extras.reserve(forward_extras.size());
                    for (const Extra& edge : forward_extras) {
                        check(); backward_extras.push_back({edge.target, edge.source, edge.cost});
                    }
                }
                for (const Extra& edge : extras) {
                    check(); int x, y, z; cell(edge.source, x, y, z);
                    rows[y * nz + z].extras.push_back(edge);
                }
                if (!extras.empty()) for (Row& row : rows) {
                    check();
                    std::stable_partition(row.extras.begin(), row.extras.end(), [&](const Extra& edge) {
                        check();
                        int x, y, z; cell(edge.target, x, y, z);
                        for (int goal : rows[y * nz + z].goals) {
                            check();
                            if (goal == x) return true;
                        }
                        return false;
                    });
                }
                if (!reverse_reach) { measure_distance_prices(); prepare_goal_segments(); }
                // Admit every zero-edge intersection before preparing any
                // non-overlapping seed; endpoint order must not consume quota.
                if (backwards) for (Index start : starts) {
                    check(); int x, y, z; cell(start, x, y, z);
                    if (!is_goal(x, y, z)) continue;
                    offer({y, z, x, x, 0.0, 0, -1, -1, 0, false, false});
                    if (best == 0.0) break;
                }
                if (policy) for (const MotionEndpoint& start : policy->initial) {
                    check(); int x, y, z; cell(start.cell, x, y, z);
                    offer({y, z, x, x, charge_occupied_cells ? price(x, y, z) : 0.0,
                           0, -1, -1, 0, false, false, 0.0, start.state});
                }
                else for (Index start : starts) {
                    if (best == 0.0) break;
                    check(); int x, y, z; cell(start, x, y, z);
                    if (backwards && is_goal(x, y, z)) continue;
                    if (reverse_reach || backwards) row(y, z, x, x);
                    if (backwards && !flags[start]) continue;
                    if (reverse_reach && !flags[start]) {
                        if (std::find(rows[y * nz + z].goals.begin(), rows[y * nz + z].goals.end(), x)
                            != rows[y * nz + z].goals.end()) best = 0.0;
                        continue;
                    }
                    offer({y, z, x, x, !backwards && charge_occupied_cells ? price(x, y, z) : 0.0,
                           0, -1, -1, 0, false, false});
                }
                while (!queue.empty()) {
                    check();
                    Entry entry = queue.top(); queue.pop();
                    if (entry.lower >= best) break;
                    Label source = labels[entry.label];
                    if (backwards && !flags[index(source.lo, source.y, source.z)]) continue;
                    std::vector<Segment> active;
                    for (const Segment& segment : envelope(source.y, source.z, source.state)) {
                        check(); ++answer.profile_scans;
                        if (segment.label == entry.label) active.push_back(segment);
                    }
                    for (const Segment& segment : active) {
                        // Cross propagation and its deferred horizontal closure
                        // are one interval operation, not two charged searches.
                        if (!entry.horizontal) { charge(); ++answer.interval_pops; }
                        if (entry.horizontal || reverse_reach) horizontal(entry.label, segment.lo, segment.hi, entry.horizontal);
                        if (entry.horizontal || entry.lower >= best) continue;
                        for (const Extra& edge : rows[source.y * nz + source.z].extras) {
                            check();
                            if (entry.lower >= best) break;
                            int x, y, z; cell(edge.source, x, y, z);
                            if (x < segment.lo || x > segment.hi) continue;
                            if (source.value(x) + edge.cost + (backwards ? source.toll : 0.0) >= best) continue;
                            int tx, ty, tz; cell(edge.target, tx, ty, tz);
                            row(ty, tz, tx, tx);
                            if (reverse_reach &&
                                std::find(rows[ty * nz + tz].goals.begin(), rows[ty * nz + tz].goals.end(), tx)
                                != rows[ty * nz + tz].goals.end()) best = 0.0;
                            if (!flags[edge.target] && !(backwards && is_goal(tx, ty, tz))) continue;
                            offer({ty, tz, tx, tx, source.value(x) + edge.cost + (backwards ? source.toll : price(tx, ty, tz)),
                                   0, entry.label, x, 0, false, false});
                        }
                        if (entry.lower >= best) continue;
                        if (policy) {
                            auto found = state_edges.find(source.state);
                            if (found != state_edges.end()) for (int edge : found->second) {
                                check();
                                const MotionEdge& choice = policy->edges[edge];
                                const auto& level = moves[source.z];
                                for (int primitive = 0; primitive < int(level.size()); ++primitive) {
                                    check();
                                    const Move& move = level[primitive];
                                    if (move.motion != choice.move) continue;
                                    if (move.dy == 0 && move.dz == 0 && !move.via && std::abs(move.dx) == 1
                                        && closure_edge(choice)) continue;
                                    cross_motion(entry.label, segment.lo, segment.hi, move, edge, primitive);
                                }
                            }
                        } else for (const Move& move : moves[source.z]) {
                            check();
                            if (entry.lower >= best) break;
                            if (move.dy == 0 && move.dz == 0 && !move.via && std::abs(move.dx) == 1) continue;
                            cross(entry.label, segment.lo, segment.hi, move);
                        }
                    }
                    if (!reverse_reach && !entry.horizontal && entry.lower < best && !active.empty())
                        queue_horizontal(entry.label, entry.lower);
                }
                if (reverse_reach && std::isfinite(best)) answer.status = 0;
                else if (goal_label >= 0) {
                    certify();
                    check(true); answer.status = 0;
                }
                else if (policy) { check(true); answer.status = 5; }
                else {
                    for (int y = 0; y < ny; ++y) for (int z = 0; z < nz; ++z)
                        for (const Segment& segment : rows[y * nz + z].envelope) {
                            check();
                            if (segment.label < 0) continue;
                            if (!transposed) answer.reached.push_back({y, z, segment.lo, segment.hi});
                            else for (int x = segment.lo; x <= segment.hi; ++x) {
                                check(); answer.reached.push_back({x, z, y, y});
                            }
                        }
                    check(true); answer.status = 2;
                }
            } catch (const Limit&) {
                answer.status = 1; answer.path.clear(); answer.reached.clear(); answer.cost = infinity;
                answer.motion.clear();
            } catch (const Cancelled&) {
                answer.status = 3; answer.path.clear(); answer.reached.clear(); answer.cost = infinity;
                answer.motion.clear();
            }
            answer.search_s = std::chrono::duration<double>(Clock::now() - began).count()
                              - answer.preparation_s - answer.certification_s;
            memory(); return std::move(answer);
        }
    };
    Answer run(int nx, int ny, int nz, const unsigned char* flags, const double* history, PyObject* history_list, double pressure,
               const std::vector<std::vector<Move>>& moves, const std::vector<Index>& starts,
               const std::vector<Index>& goals, const std::vector<Extra>& extras, Index limit, double remaining,
               const double* present, bool charge_occupied_cells, PyObject* cancelled, bool transposed,
               const Policy& policy) {
        auto began = Clock::now();
        if (policy.enabled) {
            Answer answer = Wave(nx, ny, nz, flags, history, history_list, pressure, moves, starts, goals,
                                 extras, limit, remaining, present, charge_occupied_cells, cancelled,
                                 false, false, transposed, &policy).solve();
            answer.preparation_s += std::chrono::duration<double>(Clock::now() - began).count()
                - answer.preparation_s - answer.search_s - answer.certification_s;
            return answer;
        }
        if (!goals.empty() && goals.size() < starts.size()) {
            double left = remaining < 0 ? -1 : std::max(0.0,
                remaining - std::chrono::duration<double>(Clock::now() - began).count());
            Answer answer = Wave(nx, ny, nz, flags, history, history_list, pressure, moves, goals, starts,
                                 extras, limit, left, present, charge_occupied_cells, cancelled, false, true, transposed).solve();
            if (answer.status == 2) answer.status = 4;
            answer.preparation_s += std::chrono::duration<double>(Clock::now() - began).count()
                - answer.preparation_s - answer.search_s - answer.certification_s;
            return answer;
        }
        // A bounded zero-price interval probe can prove a small destination
        // pocket sealed without traversing a large source component. It never
        // supplies a candidate path, and consumes the original shared work.
        Answer reverse;
        Index allowance = std::min<Index>(limit / 16, 1024);
        if (allowance > 0 && !starts.empty() && !goals.empty()) {
            std::vector<std::vector<Move>> incoming(nz);
            for (int z = 0; z < nz; ++z) for (const Move& move : moves[z]) {
                int target = z + move.dz;
                if (target >= 0 && target < nz)
                    incoming[target].push_back({-move.dx, -move.dy, -move.dz, move.via, 0.0});
            }
            std::vector<Extra> backwards;
            backwards.reserve(extras.size());
            for (const Extra& edge : extras) backwards.push_back({edge.target, edge.source, 0.0});
            double left = remaining < 0 ? -1 : std::max(0.0,
                remaining - std::chrono::duration<double>(Clock::now() - began).count());
            reverse = Wave(nx, ny, nz, flags, nullptr, nullptr, 0.0, incoming, goals, starts,
                           backwards, allowance, left, nullptr, false, cancelled, true, false, transposed).solve();
            if (reverse.status == 2) {
                reverse.status = 4;
                reverse.preparation_s += std::chrono::duration<double>(Clock::now() - began).count()
                    - reverse.preparation_s - reverse.search_s - reverse.certification_s;
                return reverse;
            }
            if (reverse.status == 3) return reverse;
        }
        double left = remaining < 0 ? -1 : std::max(0.0,
            remaining - std::chrono::duration<double>(Clock::now() - began).count());
        Answer answer = Wave(nx, ny, nz, flags, history, history_list, pressure, moves, starts, goals,
                             extras, limit - reverse.work, left, present, charge_occupied_cells, cancelled, false, false, transposed).solve();
        answer.work += reverse.work;
        answer.prepared_cells += reverse.prepared_cells;
        answer.interval_pops += reverse.interval_pops;
        answer.labels += reverse.labels;
        answer.offers += reverse.offers;
        answer.intersections += reverse.intersections;
        answer.profile_scans += reverse.profile_scans;
        answer.memory_bytes = std::max(answer.memory_bytes, reverse.memory_bytes);
        answer.preparation_s += reverse.preparation_s;
        answer.search_s += reverse.search_s;
        double total = std::chrono::duration<double>(Clock::now() - began).count();
        answer.preparation_s += total - answer.preparation_s - answer.search_s - answer.certification_s;
        return answer;
    }
    }
    """
    ctypedef long long Index "flab_geometry::Index"
    cdef cppclass Move "flab_geometry::Move":
        int dx, dy, dz
        bint via
        double cost
        int motion
    cdef cppclass MotionBox "flab_geometry::MotionBox":
        int xlo, xhi, ylo, yhi, zlo, zhi
    vector[MotionBox] subtract_motion_boxes "flab_geometry::free_motion_boxes"(
        const MotionBox&, const vector[MotionBox]&) except + nogil
    cdef cppclass MotionEdge "flab_geometry::MotionEdge":
        int source, move, target, guard, action
    cdef cppclass MotionEndpoint "flab_geometry::MotionEndpoint":
        Index cell
        int state, action
    cdef cppclass Policy "flab_geometry::Policy":
        bint enabled
        vector[MotionEdge] edges
        vector[vector[MotionBox]] guards
        vector[MotionEndpoint] initial, accepting
    cdef cppclass MotionTrace "flab_geometry::MotionTrace":
        Index path_index
        int edge, primitive, source, target, width
    cdef cppclass Extra "flab_geometry::Extra":
        Index source, target
        double cost
    cdef cppclass Reach "flab_geometry::Reach":
        int y, z, lo, hi
    cdef cppclass Answer "flab_geometry::Answer":
        int status
        vector[Index] path
        vector[Reach] reached
        vector[MotionTrace] motion
        int initial_state, final_state, final_action
        double cost
        Index work, prepared_cells, interval_pops, labels, offers, intersections, profile_scans
        Index certified_edges, memory_bytes
        double preparation_s, search_s, certification_s
    Answer run "flab_geometry::run"(int, int, int, const unsigned char*, const double*, PyObject*, double,
                                   const vector[vector[Move]]&, const vector[Index]&, const vector[Index]&,
                                   const vector[Extra]&, Index, double, const double*, bint, PyObject*, bint,
                                   const Policy&) except + nogil

def free_motion_boxes(domain, forbidden):
    """Subtract inclusive boxes, preserving deterministic row coalescing order."""
    if not forbidden:
        return (domain,)
    cdef MotionBox bounds
    cdef MotionBox box
    cdef vector[MotionBox] obstacles
    cdef vector[MotionBox] result
    bounds.xlo, bounds.xhi, bounds.ylo, bounds.yhi, bounds.zlo, bounds.zhi = domain
    obstacles.reserve(len(forbidden))
    for row in forbidden:
        box.xlo, box.xhi, box.ylo, box.yhi, box.zlo, box.zhi = row
        obstacles.push_back(box)
    with nogil:
        result = subtract_motion_boxes(bounds, obstacles)
    return tuple(
        (box.xlo, box.xhi, box.ylo, box.yhi, box.zlo, box.zhi)
        for box in result
    )


def search_intervals(const unsigned char[::1] flags, history, double pressure,
                     int nx, int ny, int nz, transitions, starts, goals, extra_edges,
                     Index max_work, deadline, present=None, bint charge_occupied_cells=False, cancelled=None,
                     motion=None):
    """Return status/path/price/component/counters/motion; status 5 is motion exhaustion."""
    cdef double began = monotonic()
    cdef double remaining = -1
    cdef const double[::1] hist
    cdef const double* history_ptr = NULL
    cdef PyObject* history_list = NULL
    cdef const double[::1] present_view
    cdef const double* present_ptr = NULL
    cdef PyObject* cancelled_ptr = NULL
    cdef vector[vector[Move]] moves
    cdef vector[Move] level_moves
    cdef vector[Index] seeds, ends
    cdef vector[Extra] extras
    cdef Move move
    cdef Extra edge
    cdef Answer result
    cdef Policy policy
    cdef MotionEdge motion_edge
    cdef MotionEndpoint endpoint
    cdef MotionBox box
    cdef vector[MotionBox] boxes
    cdef MotionTrace trace
    cdef Index boundary_work = 0
    cdef Index size = <Index>nx * ny * nz
    cdef Index node
    cdef int sxlo = nx, sxhi = -1, sylo = ny, syhi = -1
    cdef int gxlo = nx, gxhi = -1, gylo = ny, gyhi = -1
    cdef int x, y
    cdef bint transposed
    if nx <= 0 or ny <= 0 or nz <= 0 or size != flags.shape[0]:
        raise ValueError("geometric dimensions do not match occupancy")
    if present is not None:
        if len(present) != size:
            raise ValueError("geometric present congestion does not match occupancy")
        present_view = present
        present_ptr = &present_view[0]
    if cancelled is not None:
        cancelled_ptr = <PyObject*> cancelled
    if history is not None:
        if len(history) != size:
            raise ValueError("geometric history does not match occupancy")
        if isinstance(history, list):
            history_list = <PyObject*> history
        else:
            hist = history
            history_ptr = &hist[0]
    if len(transitions) != nz:
        raise ValueError("geometric transitions do not match levels")
    preparation = None
    if motion is not None:
        if extra_edges:
            raise ValueError("motion-constrained queries do not support extra graph edges")
        preparation = _MotionPreparation(max_work, deadline, cancelled)
    try:
        if preparation is not None:
            validate_motion(motion, size, preparation)
            policy.enabled = True
            shape_indices = {}
            for i, shape in enumerate(motion.moves):
                preparation()
                shape_indices[shape] = i
        for node in starts:
            if preparation is not None:
                preparation()
            if node < 0 or node >= size:
                raise ValueError("geometric start is outside occupancy")
            seeds.push_back(node)
            x, y = <int>(node // nz // ny), <int>(node // nz % ny)
            sxlo, sxhi = min(sxlo, x), max(sxhi, x)
            sylo, syhi = min(sylo, y), max(syhi, y)
        for node in goals:
            if preparation is not None:
                preparation()
            if node < 0 or node >= size:
                raise ValueError("geometric goal is outside occupancy")
            ends.push_back(node)
            x, y = <int>(node // nz // ny), <int>(node // nz % ny)
            gxlo, gxhi = min(gxlo, x), max(gxhi, x)
            gylo, gyhi = min(gylo, y), max(gyhi, y)
        # Swap XY (a reflection), keeping original flat indices and move IDs.
        transposed = (not seeds.empty() and not ends.empty()
                      and max(0, gylo - syhi, sylo - gyhi) > max(0, gxlo - sxhi, sxlo - gxhi))
        for row in transitions:
            level_moves.clear()
            for dx, dy, dz, via, cost in row:
                if preparation is not None:
                    preparation()
                    if any(type(delta) is not int or not -(2**30) <= delta <= 2**30 for delta in (dx, dy, dz)):
                        raise ValueError("motion physical displacement must be a bounded integer")
                    if type(via) is not bool or (via and (dx % 2 or dy % 2)):
                        raise ValueError("motion physical ramp via requires even XY displacements")
                    if not isfinite(cost) or cost < 0:
                        raise ValueError("motion physical movement price must be finite and nonnegative")
                    move.motion = shape_indices.get((dx, dy, dz, via), -1)
                move.dx, move.dy, move.dz = (dy if transposed else dx), (dx if transposed else dy), dz
                move.via, move.cost = via, cost
                level_moves.push_back(move)
            moves.push_back(level_moves)
        for source, edges in extra_edges.items():
            edge.source = source
            for target, cost in edges:
                edge.target, edge.cost = target, cost
                if edge.source < 0 or edge.source >= size or edge.target < 0 or edge.target >= size:
                    raise ValueError("geometric connector is outside occupancy")
                extras.push_back(edge)
        if preparation is not None:
            for choice in motion.edges:
                preparation()
                motion_edge.source, motion_edge.move, motion_edge.target = choice.source, choice.move, choice.target
                motion_edge.guard, motion_edge.action = choice.guard, choice.action
                policy.edges.push_back(motion_edge)
            for guard in motion.guards:
                preparation()
                boxes.clear()
                for xlo, xhi, ylo, yhi, zlo, zhi in guard.boxes:
                    preparation()
                    box.xlo, box.xhi = (ylo, yhi) if transposed else (xlo, xhi)
                    box.ylo, box.yhi = (xlo, xhi) if transposed else (ylo, yhi)
                    box.zlo, box.zhi = zlo, zhi
                    boxes.push_back(box)
                policy.guards.push_back(boxes)
            admitted_starts, admitted_goals = set(starts), set(goals)
            for point in motion.initial:
                preparation()
                if point.cell in admitted_starts:
                    endpoint.cell, endpoint.state, endpoint.action = point.cell, point.state, point.action
                    policy.initial.push_back(endpoint)
            for point in motion.accepting:
                preparation()
                if point.cell in admitted_goals:
                    endpoint.cell, endpoint.state, endpoint.action = point.cell, point.state, point.action
                    policy.accepting.push_back(endpoint)
            boundary_work = preparation.work
            preparation.poll(True)
    except _MotionBudget:
        return _motion_interrupted(1, preparation.work, began)
    except _MotionCancelled:
        return _motion_interrupted(3, preparation.work, began)
    if deadline is not None:
        remaining = max(0.0, deadline - monotonic())
    cdef double boundary_s = monotonic() - began
    if history_list != NULL:
        # List-backed repair histories are borrowed lazily under the GIL.
        result = run(nx, ny, nz, &flags[0], history_ptr, history_list, pressure, moves, seeds, ends, extras, max_work - boundary_work, remaining, present_ptr, charge_occupied_cells, cancelled_ptr, transposed, policy)
    else:
        with nogil:
            result = run(nx, ny, nz, &flags[0], history_ptr, NULL, pressure, moves, seeds, ends, extras, max_work - boundary_work, remaining, present_ptr, charge_occupied_cells, cancelled_ptr, transposed, policy)
    witness = None
    path = None
    if result.status == 0:
        if preparation is None:
            path = tuple(result.path)
        else:
            witness_began = monotonic()
            try:
                steps = []
                for trace in result.motion:
                    preparation.poll()
                    motion_edge = policy.edges[trace.edge]
                    steps.append(MotionStep(trace.path_index, motion_edge.move,
                                            trace.source, trace.target, motion_edge.action))
                witness = MotionWitness(result.initial_state, result.final_state, tuple(steps), result.final_action)
                path = tuple(result.path)
                preparation.poll(True)
            except _MotionBudget:
                result.status, witness, path = 1, None, None
            except _MotionCancelled:
                result.status, witness, path = 3, None, None
            result.certification_s += monotonic() - witness_began
    return (
        result.status,
        path,
        result.cost if result.status == 0 else None,
        tuple((part.y, part.z, part.lo, part.hi) for part in result.reached),
        {"charged_work": result.work + boundary_work, "prepared_cells": result.prepared_cells,
         "interval_pops": result.interval_pops, "labels": result.labels,
         "offers": result.offers, "intersections": result.intersections,
         "profile_scans": result.profile_scans, "certified_edges": result.certified_edges,
         "retained_native_bytes": result.memory_bytes, "copied_history_cells": 0,
         "preparation_s": result.preparation_s + boundary_s,
         "search_s": result.search_s, "certification_s": result.certification_s},
        witness,
    )
