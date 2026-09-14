using System.Globalization;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using Newtonsoft.Json;
using SfyExtract;

// Usage: dotnet run -- <SatisfactoryDir> <mode> [filter|out.json]
//   list    [filter]              : every Build_* package and its export names/classes (discovery)
//   props   [filter]              : the same, plus every property of every export (discovery)
//   extract <out> <directions>    : write assets.json, resolving directions with
//                                   data/native_directions.json
//   structs <names> <out>         : write struct_schemas.json for the names in <names>
//
// `filter` is a case-insensitive substring of the package path. With a filter,
// packages that are not named Build_* are searched too, which is how you look at
// a Holo_* hologram class.
if (args.Length < 2)
{
    Console.Error.WriteLine("usage: dotnet run -- <SatisfactoryDir> <list|props|extract|structs> [filter|<out> <directions>|<names> <out>]");
    return 2;
}

var gameDir = args[0];
var mode = args[1];
var arg2 = args.Length > 2 ? args[2] : "";
var arg3 = args.Length > 3 ? args[3] : "";
// A mode this tool does not have used to fall through into `extract`, which
// then wrote assets.json under whatever name arg2 happened to be. Every mode is
// named here, and anything else is a usage error before any work is done.
if (mode is not ("list" or "props" or "extract" or "structs"))
{
    Console.Error.WriteLine(
        $"unknown mode {mode.Replace("\n", " ")}\n" +
        "usage: dotnet run -- <SatisfactoryDir> <list|props|extract|structs> [filter|<out> <directions>|<names> <out>]");
    return 2;
}
// `extract` needs both of its file arguments, and the check belongs here rather
// than after the paks are mounted: a missing argument should cost a line of
// output, not the minute the mount and the package scan take.
if (mode == "extract" && arg3.Length == 0)
{
    Console.Error.WriteLine(
        "usage: dotnet run -- <SatisfactoryDir> extract <out.json> <native_directions.json>\n" +
        "  the second file is written by scripts/sfy_native_directions.py: without it no\n" +
        "  port whose asset omits its direction could be resolved from the game at all.");
    return 2;
}
var filter = mode is "extract" or "structs" ? "" : arg2;
var paks = Path.Combine(gameDir, "FactoryGame", "Content", "Paks");
var usmap = Path.Combine(gameDir, "CommunityResources", "FactoryGame.usmap");

var mappings = UsmapCompat.Patch(usmap, out var patchedTypes);
Console.Error.WriteLine($"usmap {usmap} -> {mappings} ({patchedTypes} OptionalProperty leaves patched)");

// ---- structs ----------------------------------------------------------------
// The mappings alone answer this one, so it neither mounts the paks nor reads a
// package: the schemas are the usmap's own reflection data, not an asset's.
if (mode == "structs")
{
    if (arg2.Length == 0 || arg3.Length == 0)
    {
        Console.Error.WriteLine("usage: dotnet run -- <SatisfactoryDir> structs <names-file> <out.json>");
        return 2;
    }
    return StructSchemas.Write(mappings, usmap, arg2, arg3);
}

var provider = new DefaultFileProvider(
    paks, SearchOption.AllDirectories, new VersionContainer(EGame.GAME_UE5_6), StringComparer.OrdinalIgnoreCase);
var usmapMappings = new FileUsmapTypeMappingsProvider(mappings);
provider.MappingsContainer = usmapMappings;
provider.Initialize();
provider.Mount();
var archives = provider.MountedVfs.Select(v => $"{v.Name}:{v.FileCount}").OrderBy(s => s, StringComparer.Ordinal).ToList();
Console.Error.WriteLine($"mounted {string.Join(", ", archives)}");
foreach (var vfs in provider.MountedVfs.Where(v => v.IsEncrypted))
    Console.Error.WriteLine($"WARNING: {vfs.Name} is encrypted; an AES key would be needed");

var buildPackages = provider.Files.Keys
    .Where(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase)
             && (filter.Length > 0 || Path.GetFileNameWithoutExtension(k).StartsWith("Build_", StringComparison.Ordinal))
             && k.Contains(filter, StringComparison.OrdinalIgnoreCase))
    .OrderBy(k => k, StringComparer.Ordinal).ToList();
Console.Error.WriteLine($"{buildPackages.Count} packages to read");

if (mode is "list" or "props")
{
    foreach (var pkg in buildPackages)
    {
        Console.WriteLine(pkg);
        foreach (var export in Exports(pkg))
        {
            var super = SuperClass(export) is { } parent ? $" : super={parent.Name}" : "";
            Console.WriteLine($"  {export.Name} [{export.GetType().Name}] : {export.Class?.Name} : outer={export.Outer?.Name}{super}");
            if (mode != "props") continue;
            foreach (var property in export.Properties)
                Console.WriteLine($"      .{property.Name.Text} = {property.Tag?.GenericValue}");
        }
    }
    return 0;
}

// ---- extract ----------------------------------------------------------------

// What a connection component carries when no asset in its chain says. Written
// by `scripts/sfy_native_directions.py` out of the shipped DLL's constructors;
// see `DirectionOf` for why an omitted property is not the enum's zero. That
// both arguments are present was checked before the paks were mounted.
var nativeDirectionsText = File.ReadAllText(arg3);
var nativeDirections = JsonConvert.DeserializeObject<NativeDirections>(nativeDirectionsText)
    ?? throw new InvalidOperationException($"{arg3} is not a native-directions file");
var componentDefaults = nativeDirections.ComponentDefaults.ToDictionary(d => d.ComponentClass, StringComparer.Ordinal);
Console.Error.WriteLine(
    $"native direction defaults from {Path.GetFileName(arg3)}: " +
    string.Join(", ", componentDefaults.Select(d => $"{d.Key}={d.Value.Direction}")) + "; " +
    string.Join(", ", nativeDirections.OwnerDefaults.Select(
        o => $"{string.Join('/', o.OwnerClasses)}={o.Direction}")));

var ports = new SortedDictionary<string, List<object>>(StringComparer.Ordinal);
var holograms = new SortedDictionary<string, Dictionary<string, object?>>(StringComparer.Ordinal);
var conveyorConnections = new SortedDictionary<string, Dictionary<string, string>>(StringComparer.Ordinal);
var meshBounds = new SortedDictionary<string, Dictionary<string, object?>>(StringComparer.Ordinal);
var unmodelled = new SortedSet<string>(StringComparer.Ordinal);

foreach (var pkg in buildPackages)
{
    var exports = Exports(pkg).ToList();
    foreach (var export in exports)
    {
        var cls = export.Class?.Name.Text ?? "";
        if (cls.Contains("Connection", StringComparison.Ordinal) && KindOf(export) is null)
            unmodelled.Add(cls);
    }
    var generatedClass = exports.FirstOrDefault(e => e.Class?.Name.Text == "BlueprintGeneratedClass");
    if (generatedClass is null) continue;
    var className = generatedClass.Name;

    ports[className] = CollectPorts(generatedClass, exports);
    var bounds = MeshBounds(generatedClass, exports);
    if (bounds is not null) meshBounds[className] = bounds;

    var cdo = exports.FirstOrDefault(e => e.Name == "Default__" + className);
    if (cdo is null) continue;
    var hologram = HologramLimits(cdo);
    if (hologram is not null) holograms[className] = hologram;
    var connections = ConveyorConnections(cdo);
    if (connections is not null) conveyorConnections[className] = connections;
}

// File.ReadAllText detects the byte-order mark, which this dump carries: it is
// UTF-16 LE, not the UTF-8 the extension suggests.
var docsText = File.ReadAllText(DocsPath(gameDir));
var (classPaths, ambiguousClasses) = ClassPaths(docsText);
var (descriptorPaths, ambiguousDescriptors) = DescriptorPaths(out var subsystemDefaults);

var output = new
{
    provenance = new
    {
        build_version = BuildVersion(gameDir),
        cue4parse = typeof(DefaultFileProvider).Assembly.GetName().Version?.ToString(),
        extracted = DateTime.UtcNow.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
        game_enum = EGame.GAME_UE5_6.ToString(),
        archives,
        usmap = Path.GetFileName(usmap),
        usmap_sha256 = Sha256(usmap),
        // The machine code behind every port whose `direction_source` is
        // "native", carried here so the registry's claim can be read without
        // the game install. `scripts/sfy_native_directions.py` wrote it.
        native_direction_defaults = JsonConvert.DeserializeObject(nativeDirectionsText),
    },
    ports,
    holograms,
    // Which component each of a conveyor's two connection members points at,
    // read off the class default object. `native_directions.json` says which
    // *member* items enter and leave by; this says what those members are
    // called, so the flow order can be attached to named ports without anyone
    // inferring the pairing from a name that ends in 0.
    conveyor_connections = conveyorConnections,
    // The local-space box of the static mesh each spline buildable repeats along
    // itself, straight off the cooked `UStaticMesh`. See `MeshBounds`.
    mesh_bounds = meshBounds,
    // What `AFGBuildableSubsystem::BeginPlay` hands a factory that does not
    // override its own shard slot counts, as the cooked Blueprint states it.
    // See `DescriptorPaths`'s `out` parameter.
    subsystem_defaults = subsystemDefaults,
    wires = WireLengths(docsText),
    class_paths = classPaths,
    class_paths_ambiguous = ambiguousClasses,
    // The same question as `class_paths`, asked of the cooked assets instead of
    // Docs.json: every Blueprint whose class chain reaches `FGItemDescriptor`,
    // at the path its own package states. See `DescriptorPaths`.
    descriptor_paths = descriptorPaths,
    descriptor_paths_ambiguous = ambiguousDescriptors,
    grid = new Dictionary<string, object?>
    {
        // No cooked asset carries AFGBuildableHologram::mGridSnapSize's default;
        // tools/sfy-native reads it out of the shipped DLL (100). The
        // per-hologram overrides that do exist (Holo_PowerPole, Holo_StreetLight,
        // both 50) are in `holograms`, and the merge puts them on the buildable.
        //
        // There is no rotation_step here. This file says what the cooked assets
        // carry, and a 90 typed into this source would have said the assets
        // carry a rotation step when they do not: the merge's own
        // PROJECT_CONSTANTS is where that number lives, with the reason it is a
        // constant rather than a game datum.
        ["mGridSnapSize"] = null,
    },
};

var outPath = arg2.Length > 0 ? arg2 : "assets.json";
File.WriteAllText(outPath, JsonConvert.SerializeObject(output, Formatting.Indented) + "\n");
Console.Error.WriteLine($"wrote {outPath}: {ports.Count} classes with ports, {holograms.Count} with a hologram class");
Console.Error.WriteLine($"{classPaths.Count} class asset paths, {ambiguousClasses.Count} class names left out as ambiguous");
Console.Error.WriteLine($"{descriptorPaths.Count} cooked item-descriptor classes, {ambiguousDescriptors.Count} left out as ambiguous");
Console.Error.WriteLine($"{meshBounds.Count} classes with a spline mesh box; subsystem defaults: {JsonConvert.SerializeObject(subsystemDefaults)}");
Console.Error.WriteLine($"connection classes left out on purpose: {string.Join(", ", unmodelled)}");
return 0;

/// Every cooked Blueprint that *is* an item descriptor, by class name, with the
/// whole asset path a blueprint names it by.
///
/// This is the second, independent reading of a fact `class_paths` already
/// carries. `class_paths` are the paths Docs.json spells out inline; these are
/// read off the cooked packages themselves -- a `BlueprintGeneratedClass`
/// export's name is the class and its own outer is the package, so
/// `/Game/FactoryGame/Resource/Parts/IronPlate/Desc_IronPlate` plus
/// `Desc_IronPlate_C` is the path, straight out of the asset. Neither leg is a
/// blueprint corpus, and `tests/sfy/test_templates.py` holds the registry's
/// `item_paths` to both.
///
/// **"Is a descriptor" is the game's class hierarchy, never a name.** Two
/// thirds of them are called `Desc_*`, but fifteen of the ones the registry
/// needs are not (`BP_EquipmentDescriptorGasmask_C`,
/// `Foundation_ConcretePolished_8x2_C` and their kin), so the filter is the
/// super chain: the Blueprint supers by name, then the shipped usmap's native
/// `SuperType` chain, until it reaches `FGItemDescriptor` --
/// `FGBuildingDescriptor -> FGBuildDescriptor -> FGItemDescriptor`,
/// `FGEquipmentDescriptor -> FGItemDescriptor`, and so on.
///
/// Every cooked package is loaded for this (about 23000 of them, 40 s), because
/// nothing narrower than "every Blueprint in the content" can answer which
/// classes derive from a native one. A class name two packages both define
/// cannot be resolved by name and is listed as ambiguous instead of guessed at.
///
/// The same scan answers a second question, which is why it has an `out`
/// parameter rather than a scan of its own: **what a factory that does not
/// override its shard slot counts gets**. `AFGBuildableFactory::BeginPlay`
/// copies `AFGBuildableSubsystem::mDefaultPotentialShardSlots` and
/// `mDefaultProductionShardSlotSize` onto every buildable whose own
/// `mOverride*` bit is clear, and the subsystem is a Blueprint, not a
/// buildable, so neither Docs.json nor the `Build_*` packages carry it. The
/// class is found the way an item descriptor is -- by its super chain reaching
/// the native `FGBuildableSubsystem` -- never by the package's name, and a
/// property the Blueprint does not override comes back null so that the merge
/// falls through to the native constructor value `tools/sfy-native` read.
(SortedDictionary<string, string>, SortedSet<string>) DescriptorPaths(
    out Dictionary<string, object?>? subsystemDefaults)
{
    const string Root = "FGItemDescriptor";
    const string SubsystemRoot = "FGBuildableSubsystem";
    var types = usmapMappings.MappingsForGame?.Types
        ?? throw new InvalidDataException($"{mappings} parsed to no mappings at all");
    var paths = new SortedDictionary<string, SortedSet<string>>(StringComparer.Ordinal);
    var supers = new Dictionary<string, string?>(StringComparer.Ordinal);
    var packageOf = new SortedDictionary<string, string>(StringComparer.Ordinal);
    var packages = provider.Files.Keys
        .Where(k => k.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase))
        .OrderBy(k => k, StringComparer.Ordinal)
        .ToList();
    Console.Error.WriteLine($"{packages.Count} packages to scan for item descriptors");
    foreach (var pkg in packages)
    {
        foreach (var export in Exports(pkg))
        {
            if (export.Class?.Name.Text != "BlueprintGeneratedClass") continue;
            var outer = export.Outer?.Name.Text;
            if (string.IsNullOrEmpty(outer)) continue;
            var name = export.Name;
            if (!paths.TryGetValue(name, out var found))
                paths[name] = found = new SortedSet<string>(StringComparer.Ordinal);
            found.Add($"{outer}.{name}");
            supers[name] = NativeSuperName(export);
            packageOf[name] = pkg;
        }
    }

    // The chain, Blueprint links first and native ones after, with a seen-set so
    // a cycle in either stops rather than spins.
    bool Reaches(string name, string root)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        for (var current = name; seen.Add(current);)
        {
            if (current == root) return true;
            var next = supers.TryGetValue(current, out var blueprint)
                ? blueprint
                : types.TryGetValue(current, out var native) ? native.SuperType : null;
            if (string.IsNullOrEmpty(next)) return false;
            current = next;
        }
        return false;
    }

    var resolved = new SortedDictionary<string, string>(StringComparer.Ordinal);
    var ambiguous = new SortedSet<string>(StringComparer.Ordinal);
    foreach (var (name, found) in paths)
    {
        if (!Reaches(name, Root)) continue;
        if (found.Count == 1) resolved[name] = found.First();
        else ambiguous.Add(name);
    }

    var subsystems = paths.Keys.Where(name => Reaches(name, SubsystemRoot)).ToList();
    subsystemDefaults = null;
    if (subsystems.Count != 1)
    {
        Console.Error.WriteLine(
            $"{subsystems.Count} cooked classes derive from {SubsystemRoot} ({string.Join(", ", subsystems)}); " +
            "the shard slot defaults stay unread and the merge falls back to the binary");
    }
    else
    {
        var className = subsystems[0];
        var package = packageOf[className];
        var cdo = Exports(package).FirstOrDefault(e => e.Name == "Default__" + className);
        subsystemDefaults = new Dictionary<string, object?>
        {
            ["class"] = className,
            ["package"] = package,
            // Absent means the Blueprint does not override the native default,
            // which is the usual case for a UPROPERTY on a cooked CDO.
            ["mDefaultPotentialShardSlots"] = cdo is null ? null : Integer(cdo, "mDefaultPotentialShardSlots"),
            ["mDefaultProductionShardSlotSize"] = cdo is null ? null : Integer(cdo, "mDefaultProductionShardSlotSize"),
        };
    }
    return (resolved, ambiguous);
}

/// The local-space box of the static mesh a spline buildable repeats along itself.
///
/// A conveyor belt carries `mMesh` (`Buildables/FGBuildableConveyorBelt.h:146`)
/// and a conveyor lift carries `mMidMesh`
/// (`Buildables/FGBuildableConveyorLift.h:224`) -- the mid section, which is the
/// piece repeated up the shaft and the one `mMeshHeight` measures, where a lift's
/// other six meshes are its ends, its bellows and its shelves. Both are
/// `EditDefaultsOnly` UPROPERTYs on the class default object, so a mark that does
/// not restate one inherits its parent Blueprint's, which is why the CDO chain is
/// walked rather than only the class's own: Mk2 through Mk6 state only `mMidMesh`.
///
/// The box itself is the cooked `UStaticMesh`'s own render bounds -- `Origin` and
/// `BoxExtent` of `RenderData.Bounds`, the axis-aligned box the game builds the
/// mesh's bounds from -- never anything measured off a blueprint. `property` says
/// which UPROPERTY answered and `mesh` the asset path it pointed at, so the claim
/// can be checked against the install.
Dictionary<string, object?>? MeshBounds(UObject generatedClass, List<UObject> exports)
{
    var current = generatedClass;
    var currentExports = exports;
    for (var depth = 0; depth < 16 && current is not null; depth++)
    {
        var cdo = currentExports.FirstOrDefault(e => e.Name == "Default__" + current.Name);
        foreach (var property in new[] { "mMesh", "mMidMesh" })
        {
            UStaticMesh? mesh;
            try { mesh = cdo?.GetOrDefault<UStaticMesh?>(property, null); }
            catch (Exception) { continue; }
            if (mesh is null) continue;
            var record = new Dictionary<string, object?>
            {
                ["property"] = property,
                ["mesh"] = $"{mesh.Owner?.Name}.{mesh.Name}",
                ["stated_on"] = current.Name,
            };
            var bounds = mesh.RenderData?.Bounds;
            if (bounds is null)
            {
                record["origin"] = null;
                record["box_extent"] = null;
                record["reason"] = "the cooked UStaticMesh carries no RenderData.Bounds";
                return record;
            }
            record["origin"] = new[] { (double)bounds.Origin.X, bounds.Origin.Y, bounds.Origin.Z };
            record["box_extent"] =
                new[] { (double)bounds.BoxExtent.X, bounds.BoxExtent.Y, bounds.BoxExtent.Z };
            record["sphere_radius"] = (double)bounds.SphereRadius;
            return record;
        }
        current = SuperClass(current);
        currentExports = current?.Owner?.GetExports().ToList() ?? [];
    }
    return null;
}

IEnumerable<UObject> Exports(string pkg)
{
    try { return provider.LoadPackage(pkg).GetExports(); }
    catch (Exception ex)
    {
        Console.Error.WriteLine($"!! {pkg}: {ex.GetType().Name}: {ex.Message}");
        return [];
    }
}

/// Every connection component template on a class, including inherited ones.
///
/// Templates appear as package exports in two shapes: the Blueprint
/// construction script's `<Name>_GEN_VARIABLE` components, outered to the
/// generated class, and the components a native constructor makes, outered to
/// the class default object. Both are read the same way. A Blueprint that
/// derives from another Blueprint inherits the parent's templates, so the super
/// chain is walked until it leaves the cooked content.
///
/// The *whole* chain is kept per port, not just the most derived template.
/// Unreal leaves a template property out whenever it equals the archetype's
/// value, and a Blueprint's archetype for an inherited component is the parent
/// Blueprint's template of the same name -- so a port that says nothing here may
/// still have its direction stated one class up. `DirectionOf` walks that.
List<object> CollectPorts(UObject generatedClass, List<UObject> exports)
{
    var chains = new Dictionary<string, List<UObject>>(StringComparer.Ordinal);
    var order = new List<string>();
    var current = generatedClass;
    var currentExports = exports;
    string? nativeSuper = null;
    for (var depth = 0; depth < 16 && current is not null; depth++)
    {
        // The last class in the chain that names a super is the deepest cooked
        // one, and what it names is the native class the chain rests on. The
        // stub CUE4Parse loads for that native class names no super of its own.
        if (NativeSuperName(current) is { } named) nativeSuper = named;
        foreach (var template in currentExports.Where(IsConnectionTemplate).OrderBy(e => e.Name, StringComparer.Ordinal))
        {
            var name = template.Name.Replace("_GEN_VARIABLE", "");
            if (!chains.TryGetValue(name, out var chain))
            {
                chains[name] = chain = [];
                order.Add(name);
            }
            chain.Add(template);
        }
        current = SuperClass(current);
        currentExports = current?.Owner?.GetExports().ToList() ?? [];
    }
    return order.Select(name => Port(name, chains[name], nativeSuper)).ToList();
}

static bool IsConnectionTemplate(UObject export) => KindOf(export) is not null;

/// The Blueprint class this one derives from, or null once the super is native.
static UObject? SuperClass(UObject generatedClass)
{
    try { return (generatedClass as UStruct)?.SuperStruct?.Load(); }
    catch (Exception) { return null; }
}

/// The name of the class this one derives from, as the asset spells it.
///
/// The last cooked class in a chain names a native C++ one -- every belt mark
/// ends at `FGBuildableConveyorBelt` -- and that is what
/// `native_directions.json`'s `owner_defaults` are matched against, which is how
/// a conveyor end gets the direction its own constructor sets rather than the
/// component class's. Unreal Header Tool drops the `A`/`U` prefix when it makes
/// the FName, so the asset says `FGBuildableConveyorBelt` where the PDB says
/// `AFGBuildableConveyorBelt`; `DirectionOf` puts it back rather than storing
/// two spellings of the same class.
static string? NativeSuperName(UObject generatedClass)
{
    try { return (generatedClass as UStruct)?.SuperStruct?.Name; }
    catch (Exception) { return null; }
}

/// One connection component, as the registry's `Port` wants it.
///
/// `max_connections` is `FGCircuitConnectionComponent::mMaxNumConnectionLinks`,
/// how many wires may end on this connection. The UPROPERTY is on the circuit
/// connection, so only a power port can carry one, and -- like `mDirection` --
/// it is serialised only where a Blueprint in the chain overrides the archetype,
/// so the chain is walked the same way `DirectionOf` walks it: the three power
/// poles say 4, 7 and 10, and a machine's power input says nothing anywhere in
/// its asset chain. That last case is emitted as null with the source
/// `"unknown"`, never as a number this tool made up; `scripts/sfy_registry.py`
/// fills it from the native constructor default in `native.json` and retags it
/// `"native"`, which is the same hand-off the grid snap size makes.
object Port(string name, List<UObject> chain, string? nativeSuper)
{
    var template = chain[0];
    var location = template.GetOrDefault("RelativeLocation", FVector.ZeroVector);
    var rotation = template.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
    var kind = KindOf(template)!;
    var (direction, source) = DirectionOf(chain, kind, nativeSuper);
    var (links, linksSource) = MaxConnectionsOf(chain, kind);
    return new
    {
        name,
        kind,
        direction,
        direction_source = source,
        translation = new[] { location.X, location.Y, location.Z },
        rotation = new[] { rotation.Pitch, rotation.Yaw, rotation.Roll },
        clearance = Number(template, "mConnectorClearance"),
        max_connections = links,
        max_connections_source = linksSource,
    };
}

/// How many wires may end on this connection, and where in the asset chain it was
/// stated. A belt or a pipe connection has no such property at all, so both
/// answers are `null`/`"unknown"` for them and stay that way through the merge.
(int?, string) MaxConnectionsOf(List<UObject> chain, string kind)
{
    if (kind != "power") return (null, "unknown");
    for (var depth = 0; depth < chain.Count; depth++)
    {
        if (Integer(chain[depth], "mMaxNumConnectionLinks") is not { } stated) continue;
        return (stated, depth == 0 ? "asset" : "asset-inherited");
    }
    return (null, "unknown");
}

/// The port kind a connection component becomes, or null if it is not one.
///
/// The whole cooked content holds exactly seven connection component classes.
/// The other three -- `FGPipeConnectionComponentHyper` (15 templates),
/// `FGTrainPlatformConnection` (12) and `FGRailroadTrackConnectionComponent`
/// (5) -- are deliberately left out: a `Port.kind` is belt, pipe or power, and
/// nothing routes hypertubes or railways yet. Leaving them out is the point;
/// matching them by name shape labelled railway connections as belts.
static string? KindOf(UObject template) => template.Class?.Name.Text switch
{
    "FGFactoryConnectionComponent" => "belt",
    "FGPipeConnectionComponent" or "FGPipeConnectionFactory" => "pipe",
    "FGPowerConnectionComponent" => "power",
    _ => null,
};

/// A connection's direction, and where in the game it was established.
///
/// Belts and pipes each have their own enum and neither is serialised when it
/// equals the value the component's **archetype** carries. That is the whole
/// difficulty: an absent `mDirection` does not mean the enum's zero, it means
/// "whatever my archetype has", and the chain of archetypes is
///
///   this Blueprint's template
///     -> the parent Blueprint's template of the same name  ("asset-inherited")
///        -> ... up the Blueprint chain ...
///           -> the subobject the native constructor made    ("native")
///
/// so this walks the chain the same way. Reading an absent value as `FCD_INPUT`
/// is what made every belt and lift end an input in the first registry: their
/// archetype is the connection `AFGBuildableConveyorBase`'s constructor creates,
/// and it sets both ends to `FCD_ANY`.
///
/// `native_directions.json` holds the last step -- every value in it is a store
/// `tools/sfy-native` read out of the shipped DLL's constructors. A port that
/// falls off the end of all of it is `"unknown"`, which is shipped as `unknown`
/// rather than guessed at from the component's name.
///
/// Power connections are circuit connections with no direction property at all;
/// `native_directions.json` says so, and they come back `("any", "native")`.
(string, string) DirectionOf(List<UObject> chain, string kind, string? nativeSuper)
{
    var componentClass = chain[0].Class?.Name.Text ?? "";
    if (kind != "power")
    {
        var member = kind == "pipe" ? "mPipeConnectionType" : "mDirection";
        for (var depth = 0; depth < chain.Count; depth++)
        {
            if (Text(chain[depth], member) is not { } stated) continue;
            var named = kind == "pipe" ? PipeDirection(stated) : BeltDirection(stated);
            return (named, depth == 0 ? "asset" : "asset-inherited");
        }
    }
    // Nothing in the asset chain says, so the value is the archetype's, which
    // is native code. A buildable whose own constructor creates and sets its
    // connections wins over the component class's default.
    // `owner_classes` are the PDB's C++ names; the asset's super is the same
    // name without UHT's `A`/`U` prefix.
    var superNames = nativeSuper is null ? [] : new[] { "A" + nativeSuper, "U" + nativeSuper };
    foreach (var owner in nativeDirections.OwnerDefaults)
    {
        if (owner.ComponentClass == componentClass
            && owner.OwnerClasses.Intersect(superNames, StringComparer.Ordinal).Any())
            return (owner.Direction, "native");
    }
    if (!componentDefaults.TryGetValue(componentClass, out var fallback)) return ("unknown", "unknown");
    // A class with no direction property at all -- a power connection -- has
    // nothing for an archetype to carry, so that answer holds however the
    // component was made.
    if (fallback.Member is null) return (fallback.Direction, "native");
    // Otherwise the component class default only answers for a Blueprint's own
    // component, whose archetype really is the component class default object. A
    // subobject a native constructor made takes that constructor's value, which
    // is the `owner_defaults` above; falling back here would have called a
    // conveyor pole's snap-only connection an input, so it is `unknown` instead.
    if (!IsNativeSubobject(chain[^1])) return (fallback.Direction, "native");
    return ("unknown", "unknown");
}

/// Whether a template is a subobject a native constructor made.
///
/// The cooked package outers those to the class default object, and a
/// Blueprint's own components -- the construction script's `_GEN_VARIABLE`
/// ones -- to the generated class. The two have different archetypes, so they
/// take their default from different places.
static bool IsNativeSubobject(UObject template) =>
    template.Outer?.Name.Text.StartsWith("Default__", StringComparison.Ordinal) == true;

static string BeltDirection(string stated) =>
    stated.EndsWith("FCD_OUTPUT", StringComparison.Ordinal) ? "output"
    : stated.EndsWith("FCD_ANY", StringComparison.Ordinal) ? "any"
    : stated.EndsWith("FCD_SNAP_ONLY", StringComparison.Ordinal) ? "snap_only"
    : "input";

static string PipeDirection(string stated) =>
    stated.EndsWith("PCT_CONSUMER", StringComparison.Ordinal) ? "input"
    : stated.EndsWith("PCT_PRODUCER", StringComparison.Ordinal) ? "output"
    : stated.EndsWith("PCT_SNAP_ONLY", StringComparison.Ordinal) ? "snap_only"
    : "any";

/// The buildable's hologram class and whatever limits its class default object sets.
///
/// Nearly all of these are native C++ constructor values that no cooked asset
/// repeats, so most entries come back null; they are written out anyway so that
/// the merge can tell "the hologram does not set this" from "no hologram".
Dictionary<string, object?>? HologramLimits(UObject cdo)
{
    UObject? hologramClass;
    try { hologramClass = cdo.GetOrDefault<UObject?>("mHologramClass", null); }
    catch (Exception) { return null; }
    if (hologramClass is null) return null;

    var defaults = hologramClass.Owner?.GetExports()
        .FirstOrDefault(e => e.Name.StartsWith("Default__", StringComparison.Ordinal));
    var limits = new Dictionary<string, object?> { ["class"] = hologramClass.Name };
    foreach (var key in new[]
             {
                 "mBendRadius", "mBendRadius2D", "mGridSnapSize", "mMaximumHeight", "mMaxIncline",
                 "mMaxSplineLength", "mMinBendRadius", "mMinimumHeight",
                 "mMinimumHeightWithVerticalConnection", "mRotationStep", "mStepHeight",
             })
        limits[key] = defaults is null ? null : Number(defaults, key);
    return limits;
}

/// The component each of a conveyor's `mConnection0`/`mConnection1` points at.
///
/// `AFGBuildableConveyorBase` declares the two members and the header states
/// that `mConnection0` is the input and `mConnection1` the output; the shipped
/// binary's `Factory_Tick` shows the same. Neither says what the *components*
/// those members hold are called, and the names the content gives them --
/// `ConveyorAny0`, `ConveyorAny1` -- are a convention, not evidence. The class
/// default object settles it: `mConnection0` is an object property referring to
/// one of the CDO's own subobject exports, and CUE4Parse hands back that
/// export, whose `Name` is the component name the registry's ports carry.
///
/// Null unless the class has both, so only conveyors appear here.
static Dictionary<string, string>? ConveyorConnections(UObject cdo)
{
    UObject? first;
    UObject? second;
    try
    {
        first = cdo.GetOrDefault<UObject?>("mConnection0", null);
        second = cdo.GetOrDefault<UObject?>("mConnection1", null);
    }
    catch (Exception) { return null; }
    if (first is null || second is null) return null;
    return new Dictionary<string, string>(StringComparer.Ordinal)
    {
        ["mConnection0"] = first.Name,
        ["mConnection1"] = second.Name,
    };
}

static string DocsPath(string gameDir) =>
    Path.Combine(gameDir, "CommunityResources", "Docs", "en-US.json");

/// Every Blueprint class Docs.json states a full asset path for, by class name.
///
/// A blueprint's cost list names an item by its whole asset path --
/// `/Game/FactoryGame/Resource/Parts/IronPlate/Desc_IronPlate.Desc_IronPlate_C`
/// -- and so does a machine's inventory filter and its `mCurrentRecipe`. The
/// class name alone, which is all `docs.json` keeps, does not give the path
/// back: the folder is not derivable from it. Docs.json spells those paths out
/// inline wherever one entry refers to another (`mIngredients`, `mProduct`,
/// `mProducedIn`, a schematic's unlocked recipes), so every path here is the
/// game's own text, matched out of the dump rather than reconstructed.
/// `scripts/sfy_registry.py` keeps the ones the registry needs and refuses if
/// one is missing.
///
/// A class name that turns up under two different packages cannot be resolved
/// by name and is listed instead: in 1.2.0 the 17 such names are icon, audio
/// and material Blueprints -- no item, recipe or buildable among them.
static (SortedDictionary<string, string>, SortedSet<string>) ClassPaths(string docsText)
{
    var byName = new SortedDictionary<string, SortedSet<string>>(StringComparer.Ordinal);
    foreach (Match match in Regex.Matches(docsText, @"/Game/[A-Za-z0-9_/\-]+\.([A-Za-z0-9_\-]+_C)\b"))
    {
        var name = match.Groups[1].Value;
        if (!byName.TryGetValue(name, out var paths))
            byName[name] = paths = new SortedSet<string>(StringComparer.Ordinal);
        paths.Add(match.Value);
    }
    var resolved = new SortedDictionary<string, string>(StringComparer.Ordinal);
    var ambiguous = new SortedSet<string>(StringComparer.Ordinal);
    foreach (var (name, paths) in byName)
    {
        if (paths.Count == 1) resolved[name] = paths.First();
        else ambiguous.Add(name);
    }
    return (resolved, ambiguous);
}

/// Maximum wire lengths, read from the game's own Docs.json class-default dump.
///
/// `AFGBuildableWire::mMaxLength` is a native default, so `Build_PowerLine_C`'s
/// cooked class default object does not carry it -- but Docs.json is a
/// reflection dump of those same class defaults and does.
static SortedDictionary<string, Dictionary<string, object?>> WireLengths(string docsText)
{
    var wires = new SortedDictionary<string, Dictionary<string, object?>>(StringComparer.Ordinal);
    dynamic groups = JsonConvert.DeserializeObject(docsText)!;
    foreach (var group in groups)
    {
        if (!((string)group.NativeClass).Contains("FGBuildableWire", StringComparison.Ordinal)) continue;
        foreach (var entry in group.Classes)
        {
            var name = (string?)entry.ClassName;
            var maxLength = (string?)entry.mMaxLength;
            if (name is null || maxLength is null) continue;
            wires[name] = new Dictionary<string, object?>
            {
                ["mMaxLength"] = double.Parse(maxLength, CultureInfo.InvariantCulture),
            };
        }
    }
    return wires;
}

static string? Text(UObject export, string name) =>
    export.Properties.FirstOrDefault(p => p.Name.Text == name)?.Tag?.GenericValue?.ToString();

static double? Number(UObject export, string name)
{
    var raw = export.Properties.FirstOrDefault(p => p.Name.Text == name)?.Tag?.GenericValue;
    return raw is null ? null : Convert.ToDouble(raw, CultureInfo.InvariantCulture);
}

static int? Integer(UObject export, string name)
{
    var raw = export.Properties.FirstOrDefault(p => p.Name.Text == name)?.Tag?.GenericValue;
    return raw is null ? null : Convert.ToInt32(raw, CultureInfo.InvariantCulture);
}

static int? BuildVersion(string gameDir)
{
    var modules = Path.Combine(gameDir, "FactoryGame", "Binaries", "Win64", "FactoryGameEGS-Win64-Shipping.modules");
    if (!File.Exists(modules)) return null;
    dynamic parsed = JsonConvert.DeserializeObject(File.ReadAllText(modules))!;
    return int.TryParse((string?)parsed.BuildId, out var id) ? id : null;
}

static string Sha256(string path)
{
    using var stream = File.OpenRead(path);
    return Convert.ToHexStringLower(SHA256.HashData(stream));
}

/// `src/flab2bp/sfy/data/native_directions.json`, as far as this tool reads it.
///
/// Everything else in the file -- the member, the offset, the enum, the value
/// and the instructions each direction was read at -- is carried through into
/// `assets.json`'s provenance untouched, so the evidence travels with the claim.
/// `JsonExtensionData` is what keeps it: the fields named here are the ones the
/// resolution uses, and the rest round-trips.
sealed class NativeDirections
{
    [JsonProperty("component_defaults")]
    public List<ComponentDefault> ComponentDefaults { get; set; } = [];

    [JsonProperty("owner_defaults")]
    public List<OwnerDefault> OwnerDefaults { get; set; } = [];

    [JsonExtensionData]
    public IDictionary<string, Newtonsoft.Json.Linq.JToken> Rest { get; set; }
        = new Dictionary<string, Newtonsoft.Json.Linq.JToken>();
}

/// What a connection component carries when no asset in its chain states one.
sealed class ComponentDefault
{
    [JsonProperty("component_class")]
    public string ComponentClass { get; set; } = "";

    [JsonProperty("direction")]
    public string Direction { get; set; } = "unknown";

    /// The property the direction lives in, or null when the class has none --
    /// a power connection is a circuit connection and has no direction at all.
    [JsonProperty("member")]
    public string? Member { get; set; }

    [JsonExtensionData]
    public IDictionary<string, Newtonsoft.Json.Linq.JToken> Rest { get; set; }
        = new Dictionary<string, Newtonsoft.Json.Linq.JToken>();
}

/// A native buildable that makes its own connections and sets their direction.
sealed class OwnerDefault
{
    [JsonProperty("owner_classes")]
    public List<string> OwnerClasses { get; set; } = [];

    [JsonProperty("component_class")]
    public string ComponentClass { get; set; } = "";

    [JsonProperty("direction")]
    public string Direction { get; set; } = "unknown";

    [JsonExtensionData]
    public IDictionary<string, Newtonsoft.Json.Linq.JToken> Rest { get; set; }
        = new Dictionary<string, Newtonsoft.Json.Linq.JToken>();
}
