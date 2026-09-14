//! Read Satisfactory's native hologram constants out of the shipped module DLL.
//!
//! Seven of the limits the placer needs -- the belt bend radius and maximum
//! incline, the four conveyor-lift heights and the hologram grid snap size --
//! are C++ constructor immediates. They are in no cooked asset, in no header
//! (they have no in-class initialiser) and in no Docs.json entry, so the only
//! place left to read them is the machine code of the shipped DLL.
//!
//! The tool takes the member offsets from the PDB's type stream, finds each
//! class's constructors among the PDB's public symbols, disassembles them, and
//! traces the constant stores through `this`. Four members whose values the
//! public headers state outright act as the oracle: if any of those does not
//! come back exactly, the extraction is not trusted and the tool exits non-zero.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{anyhow, bail, Context, Result};
use iced_x86::{
    Decoder, DecoderOptions, FlowControl, Formatter, Instruction, InstructionInfoFactory,
    IntelFormatter, MemorySizeOptions, Mnemonic, OpAccess, OpKind, Register,
};
use serde::Serialize;
use sha2::{Digest, Sha256};

// ---------------------------------------------------------------------------
// The classes and members this tool is for
// ---------------------------------------------------------------------------

/// The members read by default, in the order they are reported.
///
/// `AFGPipelineHologram`'s three radii and both `mMaxSplineLength`s are the
/// oracle: the public headers state all of them, so a run that does not
/// reproduce them has a broken store tracer, not a new finding.
const DEFAULT_MEMBERS: &[(&str, &[&str])] = &[
    (
        "AFGConveyorBeltHologram",
        &["mBendRadius", "mMaxSplineLength", "mMaxIncline"],
    ),
    (
        "AFGConveyorLiftHologram",
        &[
            "mStepHeight",
            "mMinimumHeight",
            "mMaximumHeight",
            "mMinimumHeightWithVerticalConnection",
            "mMeshHeight",
        ],
    ),
    (
        "AFGPipelineHologram",
        &[
            "mBendRadius",
            "mBendRadius2D",
            "mMinBendRadius",
            "mMaxSplineLength",
        ],
    ),
    ("AFGBuildableHologram", &["mGridSnapSize"]),
    ("AFGWireHologram", &["mMaxLength"]),
    ("AFGBuildableWire", &["mMaxLength"]),
];

// ---------------------------------------------------------------------------
// 1. The PE
// ---------------------------------------------------------------------------

/// One PE section, enough of it to map an RVA back to file bytes.
#[derive(Debug)]
struct Section {
    /// `.text`, `.rdata`, ...; the constant annotation only trusts `.rdata`.
    name: String,
    rva: u32,
    virtual_size: u32,
    raw_offset: u32,
    raw_size: u32,
}

/// `UNWIND_INFO`'s flag for "this chunk belongs to another function", whose
/// entry is the `RUNTIME_FUNCTION` stored after the unwind codes.
const UNW_FLAG_CHAININFO: u8 = 0x4;

/// A chain longer than this is a malformed or hostile `.pdata`, not a function.
const MAX_CHAIN_DEPTH: usize = 16;

/// One 12-byte `.pdata` `RUNTIME_FUNCTION`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct RuntimeFunction {
    begin: u32,
    end: u32,
    unwind: u32,
}

/// The shipped DLL: its sections, its CodeView debug record and its `.pdata`.
struct Pe {
    bytes: Vec<u8>,
    sections: Vec<Section>,
    pdb_guid: String,
    pdb_age: u32,
    pdb_name: String,
    /// `.pdata` RUNTIME_FUNCTION entries as (begin rva, end rva).
    functions: BTreeMap<u32, u32>,
    /// Every chunk of a split function, keyed by the RVA of the primary entry
    /// its unwind chain resolves to and sorted by RVA. A function MSVC did not
    /// split has one entry here, itself.
    chunks: BTreeMap<u32, Vec<(u32, u32)>>,
    /// The other direction: each chunk's own `begin` -> the primary entry its
    /// unwind chain resolves to. A primary maps to itself, so a lookup that
    /// misses means `.pdata` never mentioned the chunk at all.
    chunk_owner: BTreeMap<u32, u32>,
    /// Import address table slot RVA -> the imported symbol, as the other
    /// module mangles it. `call qword ptr [rip+K]` through one of these is a
    /// call to another DLL, and this is the only way to name it.
    imports: BTreeMap<u32, String>,
    sha256: String,
}

impl Pe {
    fn load(path: &Path) -> Result<Pe> {
        let bytes = fs::read(path).with_context(|| format!("reading {}", path.display()))?;
        let pe = goblin::pe::PE::parse(&bytes).with_context(|| format!("{}", path.display()))?;
        if !pe.is_64 {
            bail!("{} is not a 64-bit PE", path.display());
        }
        let sections = pe
            .sections
            .iter()
            .map(|s| Section {
                name: s.name().unwrap_or_default().to_string(),
                rva: s.virtual_address,
                virtual_size: s.virtual_size,
                raw_offset: s.pointer_to_raw_data,
                raw_size: s.size_of_raw_data,
            })
            .collect();

        let cv = pe
            .debug_data
            .as_ref()
            .and_then(|d| d.codeview_pdb70_debug_info.as_ref())
            .ok_or_else(|| anyhow!("{} has no CodeView PDB70 debug record", path.display()))?;
        let pdb_guid = format_guid(&cv.signature);
        let pdb_age = cv.age;
        let pdb_name =
            String::from_utf8_lossy(cv.filename.split(|b| *b == 0).next().unwrap_or(cv.filename))
                .to_string();

        let exception_dir = pe
            .header
            .optional_header
            .as_ref()
            .and_then(|o| o.data_directories.get_exception_table())
            .copied()
            .ok_or_else(|| anyhow!("{} has no exception directory", path.display()))?;

        // `Import::offset` is the import address table slot's RVA (goblin's
        // `rva` is the hint/name table entry instead, which is not what a
        // `call qword ptr [rip+K]` operand points at).
        let imports = pe
            .imports
            .iter()
            .map(|import| {
                let name = if import.name.is_empty() {
                    format!("{}!{}", import.dll, import.ordinal)
                } else {
                    import.name.to_string()
                };
                (import.offset as u32, name)
            })
            .collect();

        let mut pe = Pe {
            bytes,
            sections,
            pdb_guid,
            pdb_age,
            pdb_name,
            functions: BTreeMap::new(),
            chunks: BTreeMap::new(),
            chunk_owner: BTreeMap::new(),
            imports,
            sha256: String::new(),
        };
        pe.sha256 = hex(&Sha256::digest(&pe.bytes));
        let entries = pe.parse_pdata(exception_dir.virtual_address, exception_dir.size)?;
        pe.index_pdata(&entries);
        Ok(pe)
    }

    /// The section `rva` falls in, if any.
    fn section_of(&self, rva: u32) -> Option<&Section> {
        self.sections
            .iter()
            .find(|s| rva >= s.rva && rva < s.rva + s.virtual_size.max(s.raw_size))
    }

    /// The bytes at `rva`, at most `n` of them and never past the section.
    fn rva_to_bytes(&self, rva: u32, n: usize) -> Option<&[u8]> {
        let section = self.section_of(rva)?;
        let within = (rva - section.rva) as usize;
        if within >= section.raw_size as usize {
            return None; // uninitialised tail of the section (.bss-like)
        }
        let start = section.raw_offset as usize + within;
        if start >= self.bytes.len() {
            return None; // a truncated file
        }
        let available = (section.raw_size as usize - within).min(self.bytes.len() - start);
        Some(&self.bytes[start..start + available.min(n)])
    }

    /// `.pdata` gives every x64 function's exact `[begin, end)` RVA range.
    fn parse_pdata(&self, rva: u32, size: u32) -> Result<Vec<RuntimeFunction>> {
        let table = self
            .rva_to_bytes(rva, size as usize)
            .ok_or_else(|| anyhow!("exception directory at {rva:#x} is outside every section"))?;
        Ok(table
            .as_chunks::<12>()
            .0
            .iter()
            .map(|entry| RuntimeFunction {
                begin: u32::from_le_bytes(entry[0..4].try_into().unwrap()),
                end: u32::from_le_bytes(entry[4..8].try_into().unwrap()),
                unwind: u32::from_le_bytes(entry[8..12].try_into().unwrap()),
            })
            .collect())
    }

    /// Turn the `.pdata` array into the two lookups the rest of the tool uses:
    /// `functions` (begin -> end) and `chunks` (primary -> every chunk).
    ///
    /// MSVC splits a function into chunks and gives each its own entry; only
    /// the first is the function, and the others say so by chaining their
    /// `UNWIND_INFO` back to it. Grouping by the primary each chain resolves
    /// to puts the pieces back together whatever order `.pdata` lists them in
    /// -- a chunk can and does sort before the entry it belongs to.
    fn index_pdata(&mut self, entries: &[RuntimeFunction]) {
        self.functions = entries
            .iter()
            .filter(|entry| entry.end > entry.begin)
            .map(|entry| (entry.begin, entry.end))
            .collect();
        let mut chunks: BTreeMap<u32, Vec<(u32, u32)>> = BTreeMap::new();
        let mut owner: BTreeMap<u32, u32> = BTreeMap::new();
        for entry in entries.iter().filter(|entry| entry.end > entry.begin) {
            let primary = self.primary_of(*entry);
            chunks
                .entry(primary)
                .or_default()
                .push((entry.begin, entry.end));
            owner.insert(entry.begin, primary);
        }
        for list in chunks.values_mut() {
            list.sort_unstable();
            list.dedup();
        }
        self.chunks = chunks;
        self.chunk_owner = owner;
    }

    /// The RVA of the entry `entry`'s unwind chain resolves to, transitively.
    ///
    /// An unchained entry is its own primary, which is why every function has
    /// a `chunks` list even when nothing was split.
    ///
    /// A walk that stops at the depth cap, or on a cycle, has *not* found the
    /// primary: the RVA it returns is a chunk partway up the chain, and the
    /// chunks grouped under it are some of the function rather than all of it.
    /// That is a malformed `.pdata`, so it says so on stderr rather than
    /// quietly reporting a smaller function.
    fn primary_of(&self, entry: RuntimeFunction) -> u32 {
        let mut current = entry;
        let mut seen = Vec::new();
        for _ in 0..MAX_CHAIN_DEPTH {
            if seen.contains(&current.unwind) {
                eprintln!(
                    "warning: the unwind chain from {:#x} loops back to {:#x} after {} links; \
                     stopping at {:#x}, which may be part of a larger function",
                    entry.begin,
                    current.unwind,
                    seen.len(),
                    current.begin
                );
                return current.begin; // a cycle: stop where we are rather than loop
            }
            seen.push(current.unwind);
            match self.chained_parent(current.unwind) {
                Some(parent) => current = parent,
                None => return current.begin,
            }
        }
        eprintln!(
            "warning: the unwind chain from {:#x} is longer than {MAX_CHAIN_DEPTH} links; \
             stopping at {:#x}, which may be part of a larger function",
            entry.begin, current.begin
        );
        current.begin
    }

    /// The `RUNTIME_FUNCTION` a chained `UNWIND_INFO` names as its parent.
    ///
    /// `UNWIND_INFO` is `version | flags << 3`, the prolog size, the count of
    /// unwind codes and the frame register, then `count` 2-byte codes padded
    /// to an even count -- and, with `UNW_FLAG_CHAININFO` set, the parent's
    /// 12-byte entry right after them.
    fn chained_parent(&self, unwind_rva: u32) -> Option<RuntimeFunction> {
        let head = self.rva_to_bytes(unwind_rva, 4)?;
        if head.len() < 4 {
            return None;
        }
        let version = head[0] & 0x7;
        if version != 1 && version != 2 {
            return None; // not an unwind info this tool understands
        }
        if (head[0] >> 3) & UNW_FLAG_CHAININFO == 0 {
            return None; // a primary entry: the chain ends here
        }
        let count = head[2] as u32;
        let at = unwind_rva.checked_add(4 + 2 * (count + count % 2))?;
        let entry = self.rva_to_bytes(at, 12)?;
        if entry.len() < 12 {
            return None;
        }
        let parent = RuntimeFunction {
            begin: u32::from_le_bytes(entry[0..4].try_into().unwrap()),
            end: u32::from_le_bytes(entry[4..8].try_into().unwrap()),
            unwind: u32::from_le_bytes(entry[8..12].try_into().unwrap()),
        };
        (parent.end > parent.begin).then_some(parent)
    }

    /// The `.pdata` `RUNTIME_FUNCTION` entry covering `rva`, as `[begin, end)`.
    ///
    /// A function's entry usually begins at its own RVA, but MSVC splits a
    /// function into chunks and gives each its own entry, so a symbol can land
    /// inside an entry rather than on it. Both cases are authoritative bounds;
    /// only a function with no entry at all (leaf functions may have none)
    /// falls back to the first `ret`.
    fn pdata_bounds(&self, rva: u32) -> Option<(u32, u32)> {
        if let Some(end) = self.functions.get(&rva) {
            return Some((rva, *end));
        }
        let (begin, end) = self.functions.range(..=rva).next_back()?;
        (rva < *end).then_some((*begin, *end))
    }
}

/// A CodeView GUID prints with the first three fields byte-swapped.
fn format_guid(raw: &[u8; 16]) -> String {
    format!(
        "{:08X}-{:04X}-{:04X}-{:02X}{:02X}-{:02X}{:02X}{:02X}{:02X}{:02X}{:02X}",
        u32::from_le_bytes(raw[0..4].try_into().unwrap()),
        u16::from_le_bytes(raw[4..6].try_into().unwrap()),
        u16::from_le_bytes(raw[6..8].try_into().unwrap()),
        raw[8],
        raw[9],
        raw[10],
        raw[11],
        raw[12],
        raw[13],
        raw[14],
        raw[15],
    )
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

// ---------------------------------------------------------------------------
// 2. The PDB
// ---------------------------------------------------------------------------

/// One data member of a class, as the PDB's type stream describes it.
#[derive(Clone, Debug)]
struct Member {
    offset: u32,
    type_name: String,
    size: usize,
}

/// One base class of another, at its offset within the derived object.
///
/// A UE actor multiply-inherits its interfaces, so a class has more than one
/// base and every base after the first sits at a non-zero offset. A member of
/// such a base is at `base.offset + member.offset` in the derived object.
#[derive(Clone, Debug)]
struct Base {
    name: String,
    offset: u32,
}

/// One class: its size, its base classes and its data members by name.
#[derive(Clone, Debug, Default)]
struct Layout {
    size: u64,
    /// The primary base -- the first the field list names, at offset 0. It is
    /// what `chain` walks and what `native.json` reports.
    base: Option<String>,
    /// Every base, primary first, each with its offset in this class.
    bases: Vec<Base>,
    members: BTreeMap<String, Member>,
}

/// Class layouts and constructor addresses, read out of the PDB.
struct PdbIndex {
    guid: String,
    age: u32,
    layouts: HashMap<String, Layout>,
    /// Class name -> every member function the PDB publishes for it.
    functions: HashMap<String, Vec<Function>>,
    /// Every function symbol, sorted; only read for the `disasm` mode, which
    /// needs the whole address space to resolve call targets.
    symbols: Vec<Symbol>,
    /// Function RVA -> the byte length the PDB's procedure record states.
    /// Read with `symbols`, and only used where `.pdata` has no entry.
    lengths: BTreeMap<u32, u32>,
}

impl PdbIndex {
    /// Open `path`, refusing a PDB that does not match `pe`'s debug record.
    ///
    /// `symbols` asks for the full function-symbol list as well: the `extract`
    /// mode never needs it and the module's is large, so it is opt-in.
    fn open(path: &Path, pe: &Pe, symbols: bool) -> Result<PdbIndex> {
        let file = fs::File::open(path).with_context(|| format!("reading {}", path.display()))?;
        let mut pdb = pdb::PDB::open(file).with_context(|| format!("{}", path.display()))?;

        let info = pdb.pdb_information()?;
        let guid = format!("{:X}", info.guid.hyphenated());
        if guid != pe.pdb_guid || info.age != pe.pdb_age {
            bail!(
                "PDB does not match the DLL: PDB is {} age {}, the DLL's debug record names {} age {} ({})",
                guid,
                info.age,
                pe.pdb_guid,
                pe.pdb_age,
                pe.pdb_name,
            );
        }

        let layouts = read_layouts(&mut pdb)?;
        let want_symbols = symbols;
        let (functions, symbols) = read_functions(&mut pdb, want_symbols)?;
        let lengths = if want_symbols {
            read_procedure_lengths(&mut pdb)?
        } else {
            BTreeMap::new()
        };
        Ok(PdbIndex {
            guid,
            age: info.age,
            layouts,
            functions,
            symbols,
            lengths,
        })
    }

    /// The class chain from `class` up through its base classes.
    fn chain(&self, class: &str) -> Vec<String> {
        let mut chain = Vec::new();
        let mut seen = HashSet::new();
        let mut current = Some(class.to_string());
        while let Some(name) = current {
            if !seen.insert(name.clone()) {
                break;
            }
            current = self.layouts.get(&name).and_then(|l| l.base.clone());
            chain.push(name);
        }
        chain
    }
}

/// Every class's members and first base, resolving forward references.
fn read_layouts<S: pdb::Source<'static> + 'static>(
    pdb: &mut pdb::PDB<'static, S>,
) -> Result<HashMap<String, Layout>> {
    use pdb::FallibleIterator;

    let type_information = pdb.type_information()?;
    let mut finder = type_information.finder();

    // Pass one: index every record so a field list can be looked up by index,
    // and collect the defining (non-forward-reference) class records.
    let mut classes: Vec<(String, pdb::ClassType)> = Vec::new();
    let mut iter = type_information.iter();
    while let Some(item) = iter.next()? {
        finder.update(&iter);
        if let Ok(pdb::TypeData::Class(class)) = item.parse() {
            if class.properties.forward_reference() || class.fields.is_none() {
                continue;
            }
            classes.push((class.name.to_string().into_owned(), class));
        }
    }

    let mut layouts: HashMap<String, Layout> = HashMap::new();
    for (name, class) in classes {
        let fields = match class.fields {
            Some(index) => index,
            None => continue,
        };
        let mut layout = Layout {
            size: class.size,
            base: None,
            bases: Vec::new(),
            members: BTreeMap::new(),
        };
        let mut next = Some(fields);
        while let Some(index) = next.take() {
            let item = match finder.find(index) {
                Ok(item) => item,
                Err(_) => break,
            };
            let list = match item.parse() {
                Ok(pdb::TypeData::FieldList(list)) => list,
                _ => break,
            };
            for field in &list.fields {
                match field {
                    pdb::TypeData::Member(member) => {
                        let (type_name, size) = describe_type(&finder, member.field_type);
                        layout.members.insert(
                            member.name.to_string().into_owned(),
                            Member {
                                offset: member.offset as u32,
                                type_name,
                                size,
                            },
                        );
                    }
                    pdb::TypeData::BaseClass(base) => {
                        if let Some(base_name) = type_name_of(&finder, base.base_class) {
                            // The first base is the primary one, at offset 0;
                            // `base` (and so `native.json`) names only that.
                            if layout.base.is_none() {
                                layout.base = Some(base_name.clone());
                            }
                            layout.bases.push(Base {
                                name: base_name,
                                offset: base.offset,
                            });
                        }
                    }
                    _ => {}
                }
            }
            next = list.continuation;
        }
        // A class can appear more than once (one record per translation unit);
        // keep the richest one.
        let better = layouts
            .get(&name)
            .is_none_or(|old| old.members.len() < layout.members.len());
        if better {
            layouts.insert(name, layout);
        }
    }
    Ok(layouts)
}

/// The name of the type at `index`, for base classes.
fn type_name_of(
    finder: &pdb::ItemFinder<'_, pdb::TypeIndex>,
    index: pdb::TypeIndex,
) -> Option<String> {
    match finder.find(index).ok()?.parse().ok()? {
        pdb::TypeData::Class(class) => Some(class.name.to_string().into_owned()),
        _ => None,
    }
}

/// A member's type as `(name, size in bytes)`; unknown types get size 0.
fn describe_type(
    finder: &pdb::ItemFinder<'_, pdb::TypeIndex>,
    index: pdb::TypeIndex,
) -> (String, usize) {
    let Ok(item) = finder.find(index) else {
        return ("?".to_string(), 0);
    };
    let Ok(data) = item.parse() else {
        return ("?".to_string(), 0);
    };
    match data {
        pdb::TypeData::Primitive(primitive) if primitive.indirection.is_none() => {
            primitive_name(primitive.kind)
        }
        pdb::TypeData::Primitive(_) => ("pointer".to_string(), 8),
        pdb::TypeData::Pointer(_) => ("pointer".to_string(), 8),
        pdb::TypeData::Enumeration(e) => {
            let (_, size) = describe_type(finder, e.underlying_type);
            (format!("enum {}", e.name.to_string()), size)
        }
        pdb::TypeData::Class(class) => (class.name.to_string().into_owned(), class.size as usize),
        pdb::TypeData::Modifier(m) => describe_type(finder, m.underlying_type),
        pdb::TypeData::Array(array) => {
            let (inner, _) = describe_type(finder, array.element_type);
            let size = array.dimensions.last().copied().unwrap_or(0) as usize;
            (format!("{inner}[]"), size)
        }
        _ => ("?".to_string(), 0),
    }
}

fn primitive_name(kind: pdb::PrimitiveKind) -> (String, usize) {
    use pdb::PrimitiveKind as K;
    match kind {
        K::F32 => ("float".to_string(), 4),
        K::F64 => ("double".to_string(), 8),
        K::I8 | K::Char => ("int8".to_string(), 1),
        K::U8 | K::UChar | K::RChar => ("uint8".to_string(), 1),
        K::Bool8 => ("bool".to_string(), 1),
        K::I16 | K::Short => ("int16".to_string(), 2),
        K::U16 | K::UShort => ("uint16".to_string(), 2),
        K::I32 | K::Long => ("int32".to_string(), 4),
        K::U32 | K::ULong => ("uint32".to_string(), 4),
        K::I64 | K::Quad => ("int64".to_string(), 8),
        K::U64 | K::UQuad => ("uint64".to_string(), 8),
        other => (format!("{other:?}"), 0),
    }
}

/// One member function of a class, as the PDB publishes it.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Function {
    /// `AFGConveyorLiftHologram::BeginPlay`, for `set_in` and the evidence.
    name: String,
    rva: u32,
    is_constructor: bool,
}

/// One function symbol of the module, as the PDB publishes it.
///
/// No demangler is in this tool's crate graph, so `name` is the
/// `Class::Method` form derived from the mangling by [`member_function`] --
/// `None` for anything that is not a plain, non-templated member function
/// (free functions, operators, destructors, nested and templated scopes).
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Symbol {
    rva: u32,
    name: Option<String>,
    mangled: String,
}

impl Symbol {
    /// What to print for this symbol: its `Class::Method` form if it has one.
    fn display(&self) -> &str {
        self.name.as_deref().unwrap_or(&self.mangled)
    }

    /// The class half of `Class::Method`.
    fn class(&self) -> Option<&str> {
        self.name
            .as_deref()
            .and_then(|n| n.split_once("::"))
            .map(|(class, _)| class)
    }
}

/// Every member function the PDB publishes, by the class it is a member of.
type ClassFunctions = HashMap<String, Vec<Function>>;

/// Every member function the PDB publishes, grouped by the class it is on,
/// and -- when `want_symbols` -- every function symbol of the module.
fn read_functions<S: pdb::Source<'static> + 'static>(
    pdb: &mut pdb::PDB<'static, S>,
    want_symbols: bool,
) -> Result<(ClassFunctions, Vec<Symbol>)> {
    use pdb::FallibleIterator;

    let address_map = pdb.address_map()?;
    let globals = pdb.global_symbols()?;
    let mut functions: HashMap<String, Vec<Function>> = HashMap::new();
    let mut symbols: Vec<Symbol> = Vec::new();
    let mut iter = globals.iter();
    while let Some(symbol) = iter.next()? {
        // The global stream publishes functions as `Public`; `Procedure`
        // records are accepted as well for the PDBs that carry them there,
        // but only into the symbol list: what `extract` traces is exactly the
        // set of `Public` function symbols it has always traced.
        let (mangled, rva, public) = match symbol.parse() {
            Ok(pdb::SymbolData::Public(public)) if public.function => {
                let Some(rva) = public.offset.to_rva(&address_map) else {
                    continue;
                };
                (public.name.to_string().into_owned(), rva.0, true)
            }
            Ok(pdb::SymbolData::Procedure(procedure)) if want_symbols => {
                let Some(rva) = procedure.offset.to_rva(&address_map) else {
                    continue;
                };
                (procedure.name.to_string().into_owned(), rva.0, false)
            }
            _ => continue,
        };
        let parsed = member_function(&mangled);
        if let (true, Some((class, function, is_constructor))) = (public, &parsed) {
            functions.entry(class.clone()).or_default().push(Function {
                name: format!("{class}::{function}"),
                rva,
                is_constructor: *is_constructor,
            });
        }
        if want_symbols {
            symbols.push(Symbol {
                rva,
                name: parsed.map(|(class, function, _)| format!("{class}::{function}")),
                mangled,
            });
        }
    }
    for list in functions.values_mut() {
        list.sort();
        list.dedup();
    }
    symbols.sort();
    symbols.dedup();
    Ok((functions, symbols))
}

/// The byte length the PDB states for every function it has a procedure
/// record for: RVA -> `len`.
///
/// `.pdata` is authoritative where it has an entry, but MSVC emits none for a
/// leaf function, and stopping at that function's first `ret` cuts off every
/// later `return` statement --
/// `AFGBuildableHologram::GetRotationStep` loses three of its four. The PDB
/// still states the length: each `S_GPROC32` / `S_LPROC32` record carries
/// `len`, which is game data of exactly the kind the member offsets are, so it
/// bounds a leaf without guessing. Procedure records live in the per-module
/// symbol streams (the global stream publishes mostly `S_PUB32`, which has no
/// length), so both are walked; the first length for an address wins, and a
/// zero length is ignored rather than passed off as an empty function.
fn read_procedure_lengths<S: pdb::Source<'static> + 'static>(
    pdb: &mut pdb::PDB<'static, S>,
) -> Result<BTreeMap<u32, u32>> {
    use pdb::FallibleIterator;

    let address_map = pdb.address_map()?;
    let mut lengths: BTreeMap<u32, u32> = BTreeMap::new();

    let globals = pdb.global_symbols()?;
    collect_procedure_lengths(&mut globals.iter(), &address_map, &mut lengths)?;

    let dbi = pdb.debug_information()?;
    let mut modules = dbi.modules()?;
    while let Some(module) = modules.next()? {
        let Some(info) = pdb.module_info(&module)? else {
            continue;
        };
        collect_procedure_lengths(&mut info.symbols()?, &address_map, &mut lengths)?;
    }
    Ok(lengths)
}

/// Every `Procedure` record of one symbol iterator, into `lengths`.
fn collect_procedure_lengths(
    iter: &mut pdb::SymbolIter<'_>,
    address_map: &pdb::AddressMap<'_>,
    lengths: &mut BTreeMap<u32, u32>,
) -> Result<()> {
    use pdb::FallibleIterator;

    while let Some(symbol) = iter.next()? {
        let Ok(pdb::SymbolData::Procedure(procedure)) = symbol.parse() else {
            continue;
        };
        if procedure.len == 0 {
            continue;
        }
        if let Some(rva) = procedure.offset.to_rva(address_map) {
            lengths.entry(rva.0).or_insert(procedure.len);
        }
    }
    Ok(())
}

/// Split an MSVC member-function mangling into `(class, function, is ctor)`.
///
/// `??0AFGConveyorBeltHologram@@QEAA@...` is the constructor of
/// `AFGConveyorBeltHologram`; `?BeginPlay@AFGConveyorLiftHologram@@UEAAXXZ` is
/// its `BeginPlay`. Only a plain, non-nested, non-templated scope is accepted:
/// anything with a further `@`-separated scope, a template argument or a
/// namespace is not a class this tool is ever asked about, and decoding those
/// properly means writing a full demangler.
fn member_function(mangled: &str) -> Option<(String, String, bool)> {
    if let Some(rest) = mangled.strip_prefix("??0") {
        let (class, tail) = rest.split_once("@@")?;
        if !plain_name(class) || !tail.starts_with('Q') {
            return None;
        }
        return Some((class.to_string(), class.to_string(), true));
    }
    let rest = mangled.strip_prefix('?')?;
    if rest.starts_with('?') {
        return None; // every other `??`-operator, destructors included
    }
    let (scope, _) = rest.split_once("@@")?;
    let (function, class) = scope.split_once('@')?;
    if !plain_name(function) || !plain_name(class) {
        return None;
    }
    Some((class.to_string(), function.to_string(), false))
}

/// Where `this` is when a function is entered.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ThisIn {
    /// A member function: `this` is the first argument.
    Rcx,
    /// A static member function: there is no `this` at all.
    None,
}

impl ThisIn {
    /// The register name for the JSON, and the register to seed the tracker.
    fn register(self) -> Option<(&'static str, Register)> {
        match self {
            ThisIn::Rcx => Some(("rcx", Register::RCX)),
            ThisIn::None => None,
        }
    }
}

/// Read off the mangling whether `this` arrives at all, and where.
///
/// MSVC encodes a member function as
/// `?Method@Class@@<access><this-quals><cc><return><args>`. The access code
/// says whether it is **static**: `C`, `D`, `K`, `L`, `S` and `T` are the
/// private, protected and public static codes. A static member function has no
/// `this` at all -- `rcx` is its first ordinary argument, which for a UE
/// `exec` thunk is a `UObject*` of an entirely different class -- so nothing
/// may be annotated against it.
///
/// Every other member function has `this` in `rcx`, **including** one that
/// returns an object by value. It is worth saying why, because the opposite is
/// easy to assume: MSVC's x64 convention passes the `this` pointer first and
/// the caller's hidden return slot *second*, so an sret member function has
/// `this` in `rcx` and the slot in `rdx`. Both of this module's sret functions
/// say so plainly --
/// `AFGConveyorBeltHologram::GetAnyConnectedBuildables @0xa7a170` reads its
/// members at `[rcx+800h]` and `[rcx+810h]` while building the returned
/// `TArray` through `[rdx]`, and `FInventoryItem::GetItemClass @0x1674b0`
/// reads `[rcx+8]` and stores it to `[rdx]` before returning `rdx` in `rax`.
/// `rdx` is therefore never seeded, and a write through the return slot is
/// left unannotated rather than labelled with a member of the wrong object.
fn this_register(mangled: &str) -> ThisIn {
    // A constructor is never static.
    if mangled.starts_with("??0") {
        return ThisIn::Rcx;
    }
    let Some((_, tail)) = mangled
        .strip_prefix('?')
        .and_then(|rest| rest.split_once("@@"))
    else {
        return ThisIn::Rcx;
    };
    match tail.chars().next() {
        Some('C' | 'D' | 'K' | 'L' | 'S' | 'T') => ThisIn::None,
        _ => ThisIn::Rcx,
    }
}

/// A bare identifier: no further scope, no template argument, no operator.
fn plain_name(name: &str) -> bool {
    !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
}

// ---------------------------------------------------------------------------
// 3. Disassembly
// ---------------------------------------------------------------------------

/// One decoded instruction plus its printed form, for the evidence strings.
struct Instr {
    rva: u32,
    instruction: Instruction,
    text: String,
}

/// An evidence string is only useful if it names the operand size it stored.
fn evidence_formatter() -> IntelFormatter {
    let mut formatter = IntelFormatter::new();
    formatter
        .options_mut()
        .set_space_after_operand_separator(false);
    formatter
        .options_mut()
        .set_memory_size_options(MemorySizeOptions::Always);
    formatter
}

// ---------------------------------------------------------------------------
// 4. The store tracer
// ---------------------------------------------------------------------------

/// The value one instruction pair put at one byte of the object.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Byte {
    value: u8,
    evidence: usize,
}

/// What a function wrote into `this`: the constant bytes, and the offsets it
/// overwrote with something the tracer could not prove constant.
#[derive(Debug, Default)]
struct Stores {
    bytes: BTreeMap<u32, Byte>,
    evidence: Vec<String>,
    /// Offset -> (size, the instruction that wrote a computed value there).
    computed: BTreeMap<u32, (usize, String)>,
}

impl Stores {
    /// The instruction that last wrote a computed value over `[offset, +size)`.
    fn computed_over(&self, offset: u32, size: usize) -> Option<&str> {
        self.computed
            .iter()
            .find(|(at, (wrote, _))| **at < offset + size as u32 && offset < **at + *wrote as u32)
            .map(|(_, (_, text))| text.as_str())
    }

    /// The `size` bytes at `offset`, if every one of them was written.
    fn read(&self, offset: u32, size: usize) -> Option<(Vec<u8>, String)> {
        let mut out = Vec::with_capacity(size);
        let mut used: BTreeSet<usize> = BTreeSet::new();
        for i in 0..size {
            let byte = self.bytes.get(&(offset + i as u32))?;
            out.push(byte.value);
            used.insert(byte.evidence);
        }
        let evidence = used
            .into_iter()
            .map(|i| self.evidence[i].clone())
            .collect::<Vec<_>>()
            .join(" | ");
        Some((out, evidence))
    }
}

/// One traced function: what it is, how its end was established, what it stored.
///
/// The [`Bound`] is `disasm_chunks`' answer, carried all the way into
/// `native.json` so that every number the tracer reports says which game datum
/// bounded the function it was read from -- `.pdata`, the PDB's procedure
/// record, or, when neither knows the function, a `ret` the decoder walked to --
/// and, when the symbol is not that function's entry point, how far into it the
/// symbol sits.
type TracedFunction = (Function, Bound, Stores);

/// Every traced function of a class, by the class it is a member of.
type Traced = HashMap<String, Vec<TracedFunction>>;

/// The constant last loaded into a register, and the instruction that did it.
#[derive(Clone, Debug)]
struct Loaded {
    bytes: Vec<u8>,
    text: String,
}

/// Registers a call clobbers under the Windows x64 calling convention.
const VOLATILE: &[Register] = &[
    Register::RAX,
    Register::RCX,
    Register::RDX,
    Register::R8,
    Register::R9,
    Register::R10,
    Register::R11,
    Register::XMM0,
    Register::XMM1,
    Register::XMM2,
    Register::XMM3,
    Register::XMM4,
    Register::XMM5,
];

/// What the registers hold: which of them alias `this`, which constant each
/// last took, and which member offset each was last reloaded from.
///
/// The constructor store tracer and the `disasm` mode both step this over an
/// instruction stream -- `disasm` reads only `aliases` -- so the rules that
/// keep an alias honest (it dies the moment the register is written to
/// otherwise, or a call clobbers it) are written once, here.
struct Regs {
    aliases: HashSet<Register>,
    loaded: HashMap<Register, Loaded>,
    reloaded: HashMap<Register, (u32, usize)>,
    /// Registers holding a pointer *into* the object rather than at it:
    /// `lea rdx,[rbx+40h]` off an alias. The value is the offset the pointer
    /// stands at, or `None` when a run-time index went into it. These are not
    /// aliases -- nothing is read through them -- but a store through one lands
    /// on a member, and the tracer has to veto it rather than miss it.
    derived: HashMap<Register, Option<u32>>,
    info_factory: InstructionInfoFactory,
}

impl Regs {
    /// At the entry of a member function `this` is in `rcx` and nothing else
    /// is known.
    fn new() -> Regs {
        Regs::with_this(Some(Register::RCX))
    }

    /// The same, for a function whose `this` is elsewhere -- or nowhere, in
    /// which case nothing is ever an alias and nothing is annotated.
    fn with_this(this: Option<Register>) -> Regs {
        Regs {
            aliases: this.into_iter().collect(),
            loaded: HashMap::new(),
            reloaded: HashMap::new(),
            derived: HashMap::new(),
            info_factory: InstructionInfoFactory::new(),
        }
    }

    /// Carry the register state across one instruction.
    fn step(&mut self, instr: &Instr, pe: &Pe) {
        let i = &instr.instruction;

        // A new alias, a new constant, a reload, a derived pointer -- or none.
        let kept = match register_effect(instr, pe, &self.aliases, &self.derived) {
            Some(Effect::Alias(register)) => {
                self.aliases.insert(register);
                self.loaded.remove(&register);
                self.reloaded.remove(&register);
                self.derived.remove(&register);
                Some(register)
            }
            Some(Effect::Constant(register, value)) => {
                self.aliases.remove(&register);
                self.reloaded.remove(&register);
                self.derived.remove(&register);
                self.loaded.insert(register, value);
                Some(register)
            }
            Some(Effect::Reload(register, offset, size)) => {
                self.aliases.remove(&register);
                self.loaded.remove(&register);
                self.derived.remove(&register);
                self.reloaded.insert(register, (offset, size));
                Some(register)
            }
            Some(Effect::Derived(register, at)) => {
                self.aliases.remove(&register);
                self.loaded.remove(&register);
                self.reloaded.remove(&register);
                self.derived.insert(register, at);
                Some(register)
            }
            None => None,
        };

        if i.flow_control() == FlowControl::Call || i.flow_control() == FlowControl::IndirectCall {
            for register in VOLATILE {
                self.aliases.remove(register);
                self.loaded.remove(register);
                self.reloaded.remove(register);
                self.derived.remove(register);
            }
            return;
        }

        let info = self.info_factory.info(i);
        for used in info.used_registers() {
            if !matches!(
                used.access(),
                OpAccess::Write
                    | OpAccess::ReadWrite
                    | OpAccess::CondWrite
                    | OpAccess::ReadCondWrite
            ) {
                continue;
            }
            let register = full_register(used.register());
            if Some(register) == kept {
                continue;
            }
            self.aliases.remove(&register);
            self.loaded.remove(&register);
            self.reloaded.remove(&register);
            self.derived.remove(&register);
        }
    }
}

/// Follow `this` and the constants loaded for it through one constructor.
///
/// `this` arrives in `rcx`; MSVC usually copies it into a callee-saved register
/// before calling the base constructor, so the alias set grows on
/// `mov reg, alias` and shrinks whenever a register is written otherwise. A
/// store through any alias is recorded at its displacement.
///
/// **The walk is a straight line through the instructions in RVA order**, not a
/// walk of the control-flow graph: the last store on any branch wins, and the
/// register state at the top of a chained chunk is whatever fell out of the
/// chunk before it. `tools/sfy-native/README.md` lists what that costs and what
/// catches it. Every store it cannot read as a constant is a veto rather than a
/// silence -- including, through [`derived_store`], the two shapes that write
/// the object without naming an offset off an alias.
fn trace_stores(instrs: &[Instr], pe: &Pe) -> Stores {
    let mut stores = Stores::default();
    let mut regs = Regs::new();

    for instr in instrs {
        let i = &instr.instruction;

        // A store into the object the tracer cannot read as a constant, and
        // cannot always place either. It is a veto of whatever it could have
        // hit -- see `derived_store`.
        if let Some(reach) = derived_store(i, &regs) {
            let here = format!("{} @ {:#x}", instr.text, instr.rva);
            match reach {
                Reach::At(offset, size) => {
                    for n in 0..size as u32 {
                        stores.bytes.remove(&(offset + n));
                    }
                    stores.computed.insert(offset, (size, here));
                }
                // It could have landed on any byte this function has recorded a
                // constant for, so every one of them is vetoed where it stands.
                // What the function never tracked it cannot veto: see the
                // README's blind-spot list.
                Reach::Anywhere => {
                    for offset in stores.bytes.keys().copied().collect::<Vec<_>>() {
                        stores.bytes.remove(&offset);
                        stores.computed.insert(offset, (1, here.clone()));
                    }
                }
            }
        }

        // A store through `this` is the only thing worth recording.
        if let Some((offset, size)) = store_target(i, &regs.aliases) {
            // A value put straight back where it was read from changes
            // nothing. MSVC emits this around a callee-saved spill, and
            // counting it as a write would veto a member that is never
            // actually overwritten.
            if i.op1_kind() == OpKind::Register
                && regs.reloaded.get(&full_register(i.op1_register())) == Some(&(offset, size))
            {
                continue;
            }
            let here = format!("{} @ {:#x}", instr.text, instr.rva);
            match store_value(i, size, &regs.loaded) {
                Some((bytes, source)) => {
                    let text = match source {
                        Some(load) if !load.is_empty() => format!("{load} ; {here}"),
                        _ => here,
                    };
                    stores.evidence.push(text);
                    let evidence = stores.evidence.len() - 1;
                    for (n, value) in bytes.iter().enumerate() {
                        stores.bytes.insert(
                            offset + n as u32,
                            Byte {
                                value: *value,
                                evidence,
                            },
                        );
                    }
                    stores.computed.remove(&offset);
                }
                // A store of something the tracer cannot prove constant. It is
                // still a store: whatever a constant put here before does not
                // survive it.
                None => {
                    for n in 0..size as u32 {
                        stores.bytes.remove(&(offset + n));
                    }
                    stores.computed.insert(offset, (size, here));
                }
            }
        }

        // Then the register effects.
        regs.step(instr, pe);
    }
    stores
}

/// The move mnemonics that leave their source operand in the destination.
fn is_move(mnemonic: Mnemonic) -> bool {
    matches!(
        mnemonic,
        Mnemonic::Mov
            | Mnemonic::Movss
            | Mnemonic::Movsd
            | Mnemonic::Movups
            | Mnemonic::Movaps
            | Mnemonic::Movupd
            | Mnemonic::Movapd
            | Mnemonic::Movdqu
            | Mnemonic::Movdqa
            | Mnemonic::Movq
            | Mnemonic::Movd
    )
}

/// XMM registers are their own full register; GPRs widen to their 64-bit form.
fn full_register(register: Register) -> Register {
    if register.is_gpr() {
        register.full_register()
    } else {
        register
    }
}

/// Where a store the tracer cannot read as a constant could have landed.
#[derive(Debug, PartialEq, Eq)]
enum Reach {
    /// Exactly `[offset, offset + size)`.
    At(u32, usize),
    /// Somewhere in the object; the tracer cannot say where.
    Anywhere,
}

/// A store into the tracked object that [`store_target`] does not see.
///
/// Two shapes, both writing through a register that points at the object:
///
/// * an **indexed** store off a `this` alias -- `mov [rbx+rax*4+10h],eax`. The
///   index is a run-time value, so which member it lands on is not in the
///   instruction.
/// * a store through a pointer **`lea`'d off** an alias -- `lea rdx,[rbx+40h]`
///   … `mov [rdx],eax`. `rdx` is not an alias, so `store_target` ignores it; it
///   still writes a member, and here it is placed when the `lea`'s offset is a
///   constant and vetoed wholesale when it is not.
///
/// Both are vetoes rather than values: nothing here can be read as a constant,
/// and a store that overwrites a constant means the constant is not the answer.
/// A store off a base that is neither an alias nor derived from one is ignored,
/// as it always was -- it is not known to touch this object at all.
fn derived_store(i: &Instruction, regs: &Regs) -> Option<Reach> {
    if i.op0_kind() != OpKind::Memory || !is_move(i.mnemonic()) {
        return None;
    }
    let base = i.memory_base();
    let indexed = i.memory_index() != Register::None;
    if regs.aliases.contains(&base) {
        // An unindexed store off an alias is `store_target`'s business.
        return indexed.then_some(Reach::Anywhere);
    }
    // A register the object was `lea`'d into. `None` there is a pointer whose
    // offset a run-time index went into: it still writes a member.
    let standing = regs.derived.get(&base).copied()?;
    let size = i.memory_size().size();
    let (Some(standing), false) = (standing, indexed) else {
        return Some(Reach::Anywhere);
    };
    if size == 0 {
        return Some(Reach::Anywhere);
    }
    // The displacement arrives sign-extended: `mov [rbx-10h],rsi` off a pointer
    // standing at 0x810 writes 0x800, and reading that as an unsigned 64-bit
    // number would throw the offset away.
    let at = standing as i64 + i.memory_displacement64() as i64;
    match u32::try_from(at) {
        Ok(offset) => Some(Reach::At(offset, size)),
        Err(_) => Some(Reach::Anywhere),
    }
}

/// `(displacement, size)` if this instruction stores through a `this` alias.
fn store_target(i: &Instruction, aliases: &HashSet<Register>) -> Option<(u32, usize)> {
    if i.op0_kind() != OpKind::Memory || i.memory_index() != Register::None {
        return None;
    }
    if !aliases.contains(&i.memory_base()) {
        return None;
    }
    if !is_move(i.mnemonic()) {
        return None;
    }
    let displacement = i.memory_displacement64();
    let size = i.memory_size().size();
    if size == 0 || displacement > u32::MAX as u64 {
        return None;
    }
    Some((displacement as u32, size))
}

/// The bytes a store writes, with the instruction that produced them.
fn store_value(
    i: &Instruction,
    size: usize,
    loaded: &HashMap<Register, Loaded>,
) -> Option<(Vec<u8>, Option<String>)> {
    match i.op1_kind() {
        OpKind::Register => {
            let source = loaded.get(&full_register(i.op1_register()))?;
            if source.bytes.len() < size {
                return None;
            }
            Some((source.bytes[..size].to_vec(), Some(source.text.clone())))
        }
        _ => {
            let immediate = immediate_of(i, 1)?;
            if size > 8 {
                return None;
            }
            Some((immediate.to_le_bytes()[..size].to_vec(), None))
        }
    }
}

/// What an instruction leaves in a register: a `this` alias, a constant, or a
/// copy of what is already at an offset of `this`.
enum Effect {
    Alias(Register),
    Constant(Register, Loaded),
    Reload(Register, u32, usize),
    /// A pointer *into* the object: `lea rdx,[rbx+40h]`. The offset, or `None`
    /// when a run-time index went into the address.
    Derived(Register, Option<u32>),
}

fn register_effect(
    instr: &Instr,
    pe: &Pe,
    aliases: &HashSet<Register>,
    derived: &HashMap<Register, Option<u32>>,
) -> Option<Effect> {
    let i = &instr.instruction;
    if i.op0_kind() != OpKind::Register {
        return None;
    }
    let destination = full_register(i.op0_register());
    let text = format!("{} @ {:#x}", instr.text, instr.rva);

    // `xorps xmm, xmm` (and its integer twin) is how MSVC writes 0.0f.
    if matches!(
        i.mnemonic(),
        Mnemonic::Xorps | Mnemonic::Xorpd | Mnemonic::Pxor
    ) && i.op1_kind() == OpKind::Register
        && i.op1_register() == i.op0_register()
    {
        return Some(Effect::Constant(
            destination,
            Loaded {
                bytes: vec![0; 16],
                text,
            },
        ));
    }
    // `xor r32, r32` likewise for an integer zero.
    if i.mnemonic() == Mnemonic::Xor
        && i.op1_kind() == OpKind::Register
        && full_register(i.op1_register()) == destination
    {
        return Some(Effect::Constant(
            destination,
            Loaded {
                bytes: vec![0; 8],
                text,
            },
        ));
    }

    match i.op1_kind() {
        // `mov rbx, rcx` carries `this` on.
        OpKind::Register if i.mnemonic() == Mnemonic::Mov => {
            if aliases.contains(&full_register(i.op1_register())) && i.op0_register().is_gpr64() {
                return Some(Effect::Alias(destination));
            }
            None
        }
        // A literal.
        OpKind::Immediate8
        | OpKind::Immediate16
        | OpKind::Immediate32
        | OpKind::Immediate64
        | OpKind::Immediate8to16
        | OpKind::Immediate8to32
        | OpKind::Immediate8to64
        | OpKind::Immediate32to64
            if i.mnemonic() == Mnemonic::Mov =>
        {
            let value = immediate_of(i, 1)?;
            Some(Effect::Constant(
                destination,
                Loaded {
                    bytes: value.to_le_bytes().to_vec(),
                    text,
                },
            ))
        }
        // `lea rdx,[rbx+40h]` off an alias, or off another such pointer, makes
        // a pointer into the object without making an alias of it: nothing is
        // read through it, but a store through it lands on a member. Tracking
        // where it stands is what lets `derived_store` veto that store instead
        // of never seeing it.
        OpKind::Memory if i.mnemonic() == Mnemonic::Lea && i.op0_register().is_gpr64() => {
            let base = i.memory_base();
            let standing = if aliases.contains(&base) {
                Some(0)
            } else {
                *derived.get(&base)?
            };
            let displacement = i.memory_displacement64();
            let at = match (standing, i.memory_index() == Register::None) {
                (Some(standing), true) if displacement <= u32::MAX as u64 => {
                    standing.checked_add(displacement as u32)
                }
                _ => None,
            };
            Some(Effect::Derived(destination, at))
        }
        // A read back of a member: `movss xmm11, [rcx+0x8ec]`.
        OpKind::Memory
            if is_move(i.mnemonic())
                && i.memory_index() == Register::None
                && aliases.contains(&i.memory_base()) =>
        {
            let size = i.memory_size().size();
            let displacement = i.memory_displacement64();
            if size == 0 || displacement > u32::MAX as u64 {
                return None;
            }
            Some(Effect::Reload(destination, displacement as u32, size))
        }
        // A constant pool read: `movss xmm0, [rip+K]`. Only a move counts --
        // `mulss xmm1,[rip+K]` reads the same kind of operand but leaves a
        // product in the register, not the constant.
        OpKind::Memory if i.is_ip_rel_memory_operand() && is_move(i.mnemonic()) => {
            let size = i.memory_size().size().clamp(1, 16);
            let target = i.ip_rel_memory_address() as u32;
            let bytes = pe.rva_to_bytes(target, size)?;
            if bytes.len() < size {
                return None;
            }
            Some(Effect::Constant(
                destination,
                Loaded {
                    bytes: bytes.to_vec(),
                    text,
                },
            ))
        }
        _ => None,
    }
}

fn immediate_of(i: &Instruction, operand: u32) -> Option<u64> {
    let kind = if operand == 0 {
        i.op0_kind()
    } else {
        i.op1_kind()
    };
    Some(match kind {
        OpKind::Immediate8 => i.immediate8() as u64,
        OpKind::Immediate16 => i.immediate16() as u64,
        OpKind::Immediate32 => i.immediate32() as u64,
        OpKind::Immediate64 => i.immediate64(),
        OpKind::Immediate8to16 => i.immediate8to16() as i64 as u64,
        OpKind::Immediate8to32 => i.immediate8to32() as i64 as u64,
        OpKind::Immediate8to64 => i.immediate8to64() as u64,
        OpKind::Immediate32to64 => i.immediate32to64() as u64,
        _ => return None,
    })
}

// ---------------------------------------------------------------------------
// 5. Resolving members against the class chain
// ---------------------------------------------------------------------------

/// One constant a function in the class chain stored into one member.
#[derive(Clone, Debug, Serialize)]
struct Found {
    /// `AFGConveyorLiftHologram::BeginPlay`.
    set_in: String,
    value: serde_json::Value,
    evidence: String,
    /// How the end of `set_in` was established -- see [`TracedFunction`]. A
    /// store is only as trustworthy as the bound on the function it sits in,
    /// and a `ret` or a `truncated` says the rest of that function was not read.
    size_source: &'static str,
    /// How far the symbol sits into the function that was decoded, when it is
    /// not that function's entry point.
    #[serde(skip_serializing_if = "Option::is_none")]
    entry_offset: Option<u32>,
    /// Why `size_source` is `truncated`. Absent exactly when it is not.
    #[serde(skip_serializing_if = "Option::is_none")]
    size_reason: Option<String>,
}

#[derive(Serialize)]
struct MemberOut {
    offset: u32,
    #[serde(rename = "type")]
    type_name: String,
    value: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    set_in: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    evidence: Option<String>,
    /// How the end of the function named in `set_in` was established -- see
    /// [`TracedFunction`]. Absent exactly when `set_in` is.
    #[serde(skip_serializing_if = "Option::is_none")]
    size_source: Option<&'static str>,
    /// How far the symbol sits into the function that was decoded, when it is
    /// not that function's entry point.
    #[serde(skip_serializing_if = "Option::is_none")]
    entry_offset: Option<u32>,
    /// Why `size_source` is `truncated`. Absent exactly when it is not.
    #[serde(skip_serializing_if = "Option::is_none")]
    size_reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    reason: Option<String>,
    /// Other functions of the chain that store a *different* constant here.
    /// A plain C++ member with no in-class initialiser is zeroed by the
    /// constructor and given its real value later, so this is where the
    /// number actually lives when `value` is a constructor's zero.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    also_set_in: Vec<Found>,
}

#[derive(Serialize)]
struct ClassOut {
    size: u64,
    base: Option<String>,
    chain: Vec<String>,
    /// Every constructor of the chain. A class has more than one when UE emits
    /// its `FVTableHelper` constructor as well: that one carries only the
    /// in-class initialisers, the real one also carries the body's assignments.
    ctor_rva: BTreeMap<String, Vec<String>>,
    members: BTreeMap<String, MemberOut>,
}

#[derive(Serialize)]
struct Provenance {
    dll: String,
    pdb: String,
    pdb_guid: String,
    pdb_age: u32,
    dll_sha256: String,
    tool: String,
}

#[derive(Serialize)]
struct Output {
    provenance: Provenance,
    classes: BTreeMap<String, ClassOut>,
}

/// A 32-bit float as the shortest decimal that reads back as the same float.
///
/// `5600.1f` compiles to `0x45AF00CD`, which widens to 5600.10009765625 as a
/// double. Rust's `f32` formatting gives back the 5600.1 the source wrote, and
/// that is the number to put in the registry.
fn f32_as_written(value: f32) -> Option<f64> {
    format!("{value}").parse::<f64>().ok()
}

/// Decode `bytes` as the member's declared type.
fn decode_value(type_name: &str, bytes: &[u8]) -> Option<serde_json::Value> {
    let number = |v: f64| serde_json::Number::from_f64(v).map(serde_json::Value::Number);
    match type_name {
        "float" => number(f32_as_written(f32::from_le_bytes(bytes.try_into().ok()?))?),
        "double" => number(f64::from_le_bytes(bytes.try_into().ok()?)),
        "int32" => Some(i32::from_le_bytes(bytes.try_into().ok()?).into()),
        "uint32" => Some(u32::from_le_bytes(bytes.try_into().ok()?).into()),
        "int64" => Some(i64::from_le_bytes(bytes.try_into().ok()?).into()),
        "uint64" => Some(u64::from_le_bytes(bytes.try_into().ok()?).into()),
        "int8" => Some(i8::from_le_bytes(bytes.try_into().ok()?).into()),
        "uint8" => Some(bytes.first().copied()?.into()),
        "bool" => Some((bytes.first().copied()? != 0).into()),
        _ => None,
    }
}

/// What the class chain does to one member: the constants it stores, and the
/// places it overwrites it with something the tracer cannot prove constant.
#[derive(Default)]
struct Writes {
    from_constructors: Vec<Found>,
    from_others: Vec<Found>,
    computed: Vec<String>,
}

/// Every function of `chain` that writes `member`.
///
/// The chain is walked most-derived first, and within a class the functions
/// come in address order, so a derived constructor is reported before the base
/// constructor it called.
fn writes_to(chain: &[String], member: &Member, traced: &Traced) -> Writes {
    let mut writes = Writes::default();
    for owner in chain {
        for (function, bound, stores) in traced.get(owner).into_iter().flatten() {
            if let Some(text) = stores.computed_over(member.offset, member.size) {
                writes.computed.push(format!("{} -- {text}", function.name));
            }
            let Some((bytes, evidence)) = stores.read(member.offset, member.size) else {
                continue;
            };
            let Some(value) = decode_value(&member.type_name, &bytes) else {
                continue;
            };
            let found = Found {
                set_in: function.name.clone(),
                value,
                evidence,
                size_source: bound.source,
                entry_offset: bound.entry_offset,
                size_reason: bound.reason.clone(),
            };
            if function.is_constructor {
                writes.from_constructors.push(found);
            } else {
                writes.from_others.push(found);
            }
        }
    }
    writes
}

/// Pick the value a member holds once the class chain has finished with it.
///
/// A member with no in-class initialiser is written twice: the constructor
/// gives it a zero and a later member function gives it the real number, so a
/// constant from outside the constructor wins over the constructor's. Anything
/// the tracer could not prove constant vetoes the whole member -- a computed
/// store means the game works the number out at run time, and no constant in
/// the binary is the answer.
fn settle(member_name: &str, writes: &Writes, out: &mut MemberOut) {
    if !writes.computed.is_empty() {
        out.reason = Some(format!(
            "{member_name} is overwritten with a computed value by {}",
            writes.computed.join("; ")
        ));
        out.also_set_in = writes
            .from_constructors
            .iter()
            .chain(writes.from_others.iter())
            .cloned()
            .collect();
        return;
    }

    let disagree = |list: &[Found]| list.iter().any(|f| f.value != list[0].value);
    let primary = if !writes.from_others.is_empty() {
        if disagree(&writes.from_others) {
            out.reason = Some(format!(
                "{member_name} is given two different constants: {}",
                writes
                    .from_others
                    .iter()
                    .map(|f| format!("{} = {}", f.set_in, f.value))
                    .collect::<Vec<_>>()
                    .join(", ")
            ));
            out.also_set_in = writes.from_others.clone();
            return;
        }
        &writes.from_others[0]
    } else if let Some(first) = writes.from_constructors.first() {
        first
    } else {
        out.reason = Some(format!(
            "no function in the class chain writes {member_name}"
        ));
        return;
    };

    out.value = Some(primary.value.clone());
    out.set_in = Some(primary.set_in.clone());
    out.evidence = Some(primary.evidence.clone());
    out.size_source = Some(primary.size_source);
    out.entry_offset = primary.entry_offset;
    out.size_reason = primary.size_reason.clone();
    out.also_set_in = writes
        .from_constructors
        .iter()
        .chain(writes.from_others.iter())
        .filter(|f| f.value != primary.value || f.set_in != primary.set_in)
        .cloned()
        .collect();
}

/// Resolve one class's requested members against its constructor chain.
fn resolve(class: &str, wanted: &[String], index: &PdbIndex, traced: &Traced) -> Result<ClassOut> {
    let layout = index
        .layouts
        .get(class)
        .ok_or_else(|| anyhow!("the PDB has no class named {class}"))?;
    let chain = index.chain(class);

    let mut ctor_rva: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for name in &chain {
        for function in index.functions.get(name).into_iter().flatten() {
            if function.is_constructor {
                ctor_rva
                    .entry(function.name.clone())
                    .or_default()
                    .push(format!("{:#x}", function.rva));
            }
        }
    }

    let mut members = BTreeMap::new();
    for name in wanted {
        let Some(member) = layout.members.get(name) else {
            bail!("class {class} has no member named {name}");
        };
        let mut out = MemberOut {
            offset: member.offset,
            type_name: member.type_name.clone(),
            value: None,
            set_in: None,
            evidence: None,
            size_source: None,
            entry_offset: None,
            size_reason: None,
            reason: None,
            also_set_in: Vec::new(),
        };
        if member.size == 0 {
            out.reason = Some(format!(
                "the PDB gives no size for type {}",
                member.type_name
            ));
            members.insert(name.clone(), out);
            continue;
        }

        settle(name, &writes_to(&chain, member, traced), &mut out);
        members.insert(name.clone(), out);
    }

    Ok(ClassOut {
        size: layout.size,
        base: layout.base.clone(),
        chain,
        ctor_rva,
        members,
    })
}

// ---------------------------------------------------------------------------
// 6. disasm: one named function, printed with what each instruction touches
// ---------------------------------------------------------------------------

/// One matched function and every instruction in it.
#[derive(Serialize)]
struct DisasmOut {
    /// `Class::Method`, derived from the mangling.
    symbol: String,
    mangled: String,
    rva: String,
    size: u32,
    /// `pdata` when one `.pdata` entry gave the bounds, `pdata-chained` when
    /// the function is split and its chained chunks were stitched back on,
    /// `pdb-procedure-length` when there is no `.pdata` entry and the PDB's
    /// procedure record stated the length, `ret` when only the first `ret`
    /// gave them, `truncated` when none did -- never a silent cut.
    size_source: &'static str,
    /// How far `rva` sits into the function that was disassembled, when the
    /// symbol is not that function's entry point. Absent when it is.
    #[serde(skip_serializing_if = "Option::is_none")]
    entry_offset: Option<u32>,
    /// Why `size_source` is `truncated`. Absent exactly when it is not.
    #[serde(skip_serializing_if = "Option::is_none")]
    size_reason: Option<String>,
    /// Every chunk `instructions` covers, in RVA order. One entry unless the
    /// function was split; `size` is the sum.
    chunks: Vec<ChunkOut>,
    /// The register `this` arrives in, or `null` for a static member function
    /// -- which gets no `member` annotation at all.
    #[serde(rename = "this")]
    this_in: Option<&'static str>,
    instructions: Vec<InstrOut>,
}

/// One `.pdata` chunk of a function, as it is reported.
#[derive(Serialize)]
struct ChunkOut {
    rva: String,
    size: u32,
}

/// One instruction, with whatever the annotator could say about it.
#[derive(Serialize)]
struct InstrOut {
    rva: String,
    bytes: String,
    text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    member: Option<MemberRef>,
    #[serde(skip_serializing_if = "Option::is_none")]
    constant: Option<ConstantOut>,
    #[serde(skip_serializing_if = "Option::is_none")]
    call: Option<String>,
    /// The imported symbol a `call`/`jmp qword ptr [rip+K]` goes to, when `K`
    /// is an import address table slot. The name is the exporting module's
    /// mangling, verbatim.
    #[serde(skip_serializing_if = "Option::is_none")]
    import: Option<String>,
}

/// The member an instruction's `[this + disp]` operand lands on.
#[derive(Clone, Debug, Serialize)]
struct MemberRef {
    class: String,
    name: String,
    offset: u32,
}

/// The `.rdata` bytes a `[rip+K]` operand points at, read every way that
/// could be meant. The caller knows the type; the tool does not.
#[derive(Debug, PartialEq, Serialize)]
struct ConstantOut {
    at: String,
    #[serde(rename = "f32")]
    as_f32: Option<f64>,
    #[serde(rename = "f64")]
    as_f64: Option<f64>,
    #[serde(rename = "i32")]
    as_i32: Option<i32>,
    /// The raw 16 bytes at the target, or as many as the section holds.
    bytes: String,
}

/// One member of the class chain, flattened for an offset lookup.
#[derive(Clone, Debug)]
struct MemberSpan {
    class: String,
    name: String,
    offset: u32,
    size: usize,
}

/// Every member of `class` and its bases, most-derived class first.
fn member_spans(index: &PdbIndex, class: &str) -> Vec<MemberSpan> {
    let mut spans = Vec::new();
    let mut seen = HashSet::new();
    collect_spans(index, class, 0, &mut seen, &mut spans);
    spans
}

/// `class`'s own members at `at`, then each base's at `at + base.offset`.
///
/// Every base is walked, not just the primary one: a UE actor inherits its
/// interfaces as secondary bases, whose members live at a non-zero offset in
/// the derived object and would otherwise be read as the wrong member (or
/// missed). The offset a span carries is the displacement off `this` of the
/// most-derived object, which is what an instruction's operand holds.
fn collect_spans(
    index: &PdbIndex,
    class: &str,
    at: u32,
    seen: &mut HashSet<String>,
    spans: &mut Vec<MemberSpan>,
) {
    if !seen.insert(class.to_string()) {
        return;
    }
    let Some(layout) = index.layouts.get(class) else {
        return;
    };
    for (name, member) in &layout.members {
        spans.push(MemberSpan {
            class: class.to_string(),
            name: name.clone(),
            offset: at + member.offset,
            size: member.size,
        });
    }
    for base in &layout.bases {
        collect_spans(index, &base.name, at + base.offset, seen, spans);
    }
}

/// The member at `displacement`: one starting exactly there, else one whose
/// bytes cover it (a field of an embedded struct reads as the struct member).
fn member_at(spans: &[MemberSpan], displacement: u32) -> Option<&MemberSpan> {
    spans
        .iter()
        .find(|span| span.offset == displacement)
        .or_else(|| {
            spans.iter().find(|span| {
                span.size > 0
                    && span.offset < displacement
                    && displacement < span.offset + span.size as u32
            })
        })
}

/// Every function symbol whose `Class::Method` name contains `needle`.
///
/// The match is a case-sensitive substring, and only symbols whose mangling
/// yields a `Class::Method` name take part: no demangler is in the crate
/// graph, so an operator, a destructor or a templated scope has no name to
/// match against.
fn find_functions(symbols: &[Symbol], needle: &str) -> Vec<Symbol> {
    let mut hits: Vec<Symbol> = symbols
        .iter()
        .filter(|symbol| symbol.name.as_deref().is_some_and(|n| n.contains(needle)))
        .cloned()
        .collect();
    hits.sort();
    // One address is one function: the linker folds identical bodies together
    // and a PDB can publish the same address twice.
    hits.dedup_by_key(|symbol| symbol.rva);
    hits
}

/// RVA -> the name to print for a call to it, preferring a `Class::Method`.
fn symbol_map(symbols: &[Symbol]) -> BTreeMap<u32, String> {
    let mut best: BTreeMap<u32, (bool, String)> = BTreeMap::new();
    for symbol in symbols {
        let derived = symbol.name.is_some();
        if let Some((have, _)) = best.get(&symbol.rva) {
            if *have || !derived {
                continue;
            }
        }
        best.insert(symbol.rva, (derived, symbol.display().to_string()));
    }
    best.into_iter()
        .map(|(rva, (_, name))| (rva, name))
        .collect()
}

/// One contiguous run of a function's code, with the bytes to decode.
struct CodeChunk {
    rva: u32,
    bytes: Vec<u8>,
}

/// How far a function was read, and what said so.
///
/// `source` is the `size_source` every record carries. The other two fields are
/// what a reader needs to check it:
///
/// * `entry_offset` is set when the requested symbol is *not* the entry point of
///   the function that was decoded -- it is inside one `.pdata` entry, or on a
///   chunk MSVC chained onto another function. What was disassembled is then the
///   whole primary function, from its `begin`, and this says how far into it the
///   symbol sits. `None` means the symbol is the function.
/// * `reason` is set exactly when `source` is `truncated`, and says what stopped
///   the decode: a range the section or the 64 KiB cap cut short, or a byte in
///   the middle of the function that is not a valid instruction.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Bound {
    source: &'static str,
    entry_offset: Option<u32>,
    reason: Option<String>,
}

impl Bound {
    fn new(source: &'static str) -> Bound {
        Bound {
            source,
            entry_offset: None,
            reason: None,
        }
    }

    /// The same bound, cut short for `reason`.
    fn truncated(&self, reason: String) -> Bound {
        Bound {
            source: "truncated",
            entry_offset: self.entry_offset,
            reason: Some(reason),
        }
    }
}

/// The chunks to decode and where those bounds came from.
///
/// Three sources, in order of how well each is evidence:
///
/// 1. `.pdata` -- authoritative. A function MSVC split is every chunk whose
///    unwind chain resolves to its entry, in RVA order, not just the one the
///    symbol is in (`pdata` / `pdata-chained`).
/// 2. the PDB's procedure record (`pdb-procedure-length`) -- MSVC emits no
///    `.pdata` entry for a leaf function, and the length the PDB states for it
///    is game data too, so a leaf is bounded rather than cut at a `ret` that
///    may be the first of several.
/// 3. the first `ret` (`ret`) -- only when neither of those knows the
///    function, and the caller is told so.
///
/// A range the section or the 64 KiB cap cuts short says `truncated` whichever
/// source it came from.
///
/// A symbol that is not an entry's `begin` is still bounded: the function is the
/// primary entry its chunk belongs to, so that whole function is decoded from
/// its own `begin` and [`Bound::entry_offset`] records how far into it the
/// symbol sits. Decoding only `[rva, end)` and calling the result `pdata` would
/// label part of a function as the whole of one.
fn disasm_chunks(pe: &Pe, lengths: &BTreeMap<u32, u32>, rva: u32) -> (Vec<CodeChunk>, Bound) {
    const CAP: usize = 0x1_0000;
    if let Some((entry_begin, entry_end)) = pe.pdata_bounds(rva) {
        // The function is the primary the symbol's chunk chains to, whether the
        // symbol is that primary's own entry point or sits inside it.
        let primary = pe
            .chunk_owner
            .get(&entry_begin)
            .copied()
            .unwrap_or(entry_begin);
        let ranges = match pe.chunks.get(&primary) {
            Some(chunks) => chunks.clone(),
            None => vec![(entry_begin, entry_end)],
        };
        let mut out = Vec::with_capacity(ranges.len());
        let mut short = None;
        for (begin, end) in ranges {
            let wanted = end.saturating_sub(begin) as usize;
            let bytes = pe
                .rva_to_bytes(begin, wanted.min(CAP))
                .unwrap_or_default()
                .to_vec();
            if bytes.len() != wanted && short.is_none() {
                short = Some(format!(
                    "the chunk at {begin:#x} is {} bytes of the {wanted} .pdata states: \
                     the section or the 64 KiB cap cut it short",
                    bytes.len()
                ));
            }
            out.push(CodeChunk { rva: begin, bytes });
        }
        let source = if out.len() > 1 {
            "pdata-chained"
        } else {
            "pdata"
        };
        let bound = Bound {
            source,
            entry_offset: (rva != primary).then(|| rva - primary),
            reason: None,
        };
        // A range the section or the cap cuts short is not the function
        // `.pdata` promised, and says so rather than passing for one.
        return (out, short.map_or(bound.clone(), |why| bound.truncated(why)));
    }
    // No `.pdata` entry. The PDB's procedure record still states the length.
    if let Some(len) = lengths.get(&rva).copied().filter(|len| *len > 0) {
        let wanted = len as usize;
        let bytes = pe
            .rva_to_bytes(rva, wanted.min(CAP))
            .unwrap_or_default()
            .to_vec();
        let bound = if bytes.len() == wanted {
            Bound::new("pdb-procedure-length")
        } else {
            Bound::new("pdb-procedure-length").truncated(format!(
                "the PDB states {wanted} bytes at {rva:#x} and only {} are readable: \
                 the section or the 64 KiB cap cut it short",
                bytes.len()
            ))
        };
        return (vec![CodeChunk { rva, bytes }], bound);
    }

    let Some(bytes) = pe.rva_to_bytes(rva, CAP) else {
        return (
            vec![CodeChunk {
                rva,
                bytes: Vec::new(),
            }],
            Bound::new("ret").truncated(format!("{rva:#x} is outside every section")),
        );
    };
    // Neither source knows this function: decode to the first `ret`.
    let mut decoder = Decoder::with_ip(64, bytes, rva as u64, DecoderOptions::NONE);
    let mut instruction = Instruction::default();
    while decoder.can_decode() {
        decoder.decode_out(&mut instruction);
        if instruction.is_invalid() {
            break;
        }
        if instruction.mnemonic() == Mnemonic::Ret {
            let end = (instruction.ip() as u32 - rva) as usize + instruction.len();
            let bytes = bytes[..end.min(bytes.len())].to_vec();
            return (vec![CodeChunk { rva, bytes }], Bound::new("ret"));
        }
    }
    let bytes = bytes.to_vec();
    let reason = format!(
        "nothing bounds {rva:#x}: no .pdata entry, no PDB procedure length, and no ret \
         in the {} bytes that were decoded",
        bytes.len()
    );
    (
        vec![CodeChunk { rva, bytes }],
        Bound::new("ret").truncated(reason),
    )
}

/// The reason the decode of `chunks` stopped short of the bytes it was given.
///
/// `decode_all` stops at the first byte that is not a valid instruction, so a
/// chunk whose decoded instructions do not cover it was only partly read --
/// which is exactly the case a byte-length check on the chunk cannot see. The
/// instructions come back grouped the way the chunks were handed out.
fn undecoded(chunks: &[CodeChunk], decoded: &[Vec<Instr>]) -> Option<String> {
    for (chunk, instrs) in chunks.iter().zip(decoded) {
        let read: usize = instrs.iter().map(|i| i.instruction.len()).sum();
        if read < chunk.bytes.len() {
            return Some(format!(
                "decoding stopped {read} bytes into the {}-byte chunk at {:#x}: the byte at \
                 {:#x} begins no instruction this decoder knows",
                chunk.bytes.len(),
                chunk.rva,
                chunk.rva as usize + read,
            ));
        }
    }
    None
}

/// Decode `buffer`, which starts at `begin`, keeping the printed form.
fn decode_all(buffer: &[u8], begin: u32) -> Vec<Instr> {
    let mut decoder = Decoder::with_ip(64, buffer, begin as u64, DecoderOptions::NONE);
    let mut formatter = evidence_formatter();
    let mut instrs = Vec::new();
    let mut instruction = Instruction::default();
    while decoder.can_decode() {
        decoder.decode_out(&mut instruction);
        if instruction.is_invalid() {
            break;
        }
        let mut text = String::new();
        formatter.format(&instruction, &mut text);
        instrs.push(Instr {
            rva: instruction.ip() as u32,
            instruction,
            text,
        });
    }
    instrs
}

/// The `.rdata` bytes a `[rip+K]` operand points at, decoded every way.
fn constant_at(pe: &Pe, target: u32) -> Option<ConstantOut> {
    // Only `.rdata`: a `[rip+K]` into `.data` or `.bss` is a mutable global,
    // and whatever happens to be in the file there is not a constant.
    if pe.section_of(target)?.name != ".rdata" {
        return None;
    }
    let bytes = pe.rva_to_bytes(target, 16)?;
    let finite = |value: f64| value.is_finite().then_some(value);
    Some(ConstantOut {
        at: format!("{target:#x}"),
        as_f32: bytes
            .get(..4)
            .and_then(|b| finite(f32::from_le_bytes(b.try_into().ok()?) as f64))
            .and_then(f32_as_written_f64),
        as_f64: bytes
            .get(..8)
            .and_then(|b| finite(f64::from_le_bytes(b.try_into().ok()?))),
        as_i32: bytes
            .get(..4)
            .map(|b| i32::from_le_bytes(b.try_into().unwrap())),
        bytes: hex(bytes),
    })
}

/// The shortest decimal that reads back as the same `f32`, as an `f64`.
fn f32_as_written_f64(value: f64) -> Option<f64> {
    f32_as_written(value as f32)
}

/// Annotate a decoded function: members through `this`, `.rdata` constants
/// and call targets.
///
/// The `this` tracking is [`Regs`], the same one the constructor store tracer
/// uses: `this` arrives in `rcx` and an alias survives only until the
/// register is written to otherwise or a call clobbers it. `regs` is the
/// caller's so a split function's chunks step one tracker in RVA order:
/// MSVC allocates registers across the whole function, so the callee-saved
/// register holding `this` in the entry chunk is the same one in the others.
fn annotate(
    pe: &Pe,
    buffer: &[u8],
    begin: u32,
    instrs: &[Instr],
    spans: &[MemberSpan],
    symbols: &BTreeMap<u32, String>,
    regs: &mut Regs,
) -> Vec<InstrOut> {
    let mut out = Vec::with_capacity(instrs.len());
    for instr in instrs {
        let i = &instr.instruction;

        let member = (i.memory_base() != Register::None
            && i.memory_index() == Register::None
            && regs.aliases.contains(&i.memory_base())
            && i.memory_displacement64() <= u32::MAX as u64)
            .then(|| member_at(spans, i.memory_displacement64() as u32))
            .flatten()
            .map(|span| MemberRef {
                class: span.class.clone(),
                name: span.name.clone(),
                offset: span.offset,
            });

        let constant = i
            .is_ip_rel_memory_operand()
            .then(|| constant_at(pe, i.ip_rel_memory_address() as u32))
            .flatten();

        let call = (i.flow_control() == FlowControl::Call && i.op0_kind() == OpKind::NearBranch64)
            .then(|| symbols.get(&(i.near_branch64() as u32)).cloned())
            .flatten();

        // Only a `call`/`jmp` through the slot: a `mov` that merely loads an
        // IAT entry is not a call to the import, and a tail-jump is.
        let import = (matches!(i.mnemonic(), Mnemonic::Call | Mnemonic::Jmp)
            && i.is_ip_rel_memory_operand())
        .then(|| pe.imports.get(&(i.ip_rel_memory_address() as u32)).cloned())
        .flatten();

        let at = (instr.rva - begin) as usize;
        let bytes = buffer
            .get(at..(at + i.len()).min(buffer.len()))
            .unwrap_or_default();
        out.push(InstrOut {
            rva: format!("{:#x}", instr.rva),
            bytes: hex(bytes),
            text: instr.text.clone(),
            member,
            constant,
            call,
            import,
        });

        regs.step(instr, pe);
    }
    out
}

/// Disassemble and annotate one matched function.
fn disasm_one(
    pe: &Pe,
    index: &PdbIndex,
    symbols: &BTreeMap<u32, String>,
    symbol: &Symbol,
) -> DisasmOut {
    let (chunks, bound) = disasm_chunks(pe, &index.lengths, symbol.rva);
    let spans = symbol
        .class()
        .map(|class| member_spans(index, class))
        .unwrap_or_default();
    let this = this_register(&symbol.mangled).register();
    // One tracker across every chunk, stepped in RVA order.
    let mut regs = Regs::with_this(this.map(|(_, register)| register));
    let decoded: Vec<Vec<Instr>> = chunks
        .iter()
        .map(|chunk| decode_all(&chunk.bytes, chunk.rva))
        .collect();
    // A chunk the decoder could not read to the end of is a cut the byte
    // lengths cannot see, so the bound says `truncated` for that reason too.
    let bound = match undecoded(&chunks, &decoded) {
        Some(why) => bound.truncated(why),
        None => bound,
    };
    let mut instructions = Vec::new();
    for (chunk, instrs) in chunks.iter().zip(&decoded) {
        instructions.extend(annotate(
            pe,
            &chunk.bytes,
            chunk.rva,
            instrs,
            &spans,
            symbols,
            &mut regs,
        ));
    }
    DisasmOut {
        symbol: symbol.display().to_string(),
        mangled: symbol.mangled.clone(),
        rva: format!("{:#x}", symbol.rva),
        size: chunks.iter().map(|c| c.bytes.len() as u32).sum(),
        size_source: bound.source,
        entry_offset: bound.entry_offset,
        size_reason: bound.reason,
        chunks: chunks
            .iter()
            .map(|c| ChunkOut {
                rva: format!("{:#x}", c.rva),
                size: c.bytes.len() as u32,
            })
            .collect(),
        this_in: this.map(|(name, _)| name),
        instructions,
    }
}

fn run_disasm(args: &DisasmArgs) -> Result<()> {
    let pe = Pe::load(&args.dll)?;
    let index = PdbIndex::open(&args.pdb, &pe, true)?;
    let symbols = symbol_map(&index.symbols);
    let hits = find_functions(&index.symbols, &args.symbol);
    if hits.is_empty() {
        bail!(
            "no function symbol's Class::Method name contains {:?}",
            args.symbol
        );
    }
    let out: Vec<DisasmOut> = hits
        .iter()
        .map(|symbol| disasm_one(&pe, &index, &symbols, symbol))
        .collect();
    for function in &out {
        println!(
            "{} @ {} ({} bytes from {}, {} instructions)",
            function.symbol,
            function.rva,
            function.size,
            function.size_source,
            function.instructions.len()
        );
    }
    fs::write(&args.out, serde_json::to_string_pretty(&out)? + "\n")
        .with_context(|| format!("writing {}", args.out.display()))
}

// ---------------------------------------------------------------------------
// 7. main
// ---------------------------------------------------------------------------

struct Args {
    dll: PathBuf,
    pdb: PathBuf,
    out: PathBuf,
    classes: Vec<(String, Vec<String>)>,
    expect: Vec<(String, String, f64)>,
    dump: Option<String>,
}

/// `disasm` asks for one named function rather than a member sweep.
struct DisasmArgs {
    dll: PathBuf,
    pdb: PathBuf,
    /// A case-sensitive substring of a `Class::Method` name.
    symbol: String,
    out: PathBuf,
}

/// The two things the tool does.
enum Mode {
    Extract(Args),
    Disasm(DisasmArgs),
}

const USAGE: &str = "usage: sfy-native <dll> <pdb> <out.json> \
[--class Class[:member,member]] [--expect Class.member=value] [--dump Class]\n\
       sfy-native <dll> <pdb> disasm <symbol-substring> --out <json>";

fn parse_args(argv: &[String]) -> Result<Mode> {
    let mut positional = Vec::new();
    let mut classes: Vec<(String, Vec<String>)> = Vec::new();
    let mut expect = Vec::new();
    let mut dump = None;
    let mut out: Option<PathBuf> = None;
    let mut it = argv.iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--class" => {
                let value = it.next().ok_or_else(|| anyhow!("--class needs a value"))?;
                let (name, members) = match value.split_once(':') {
                    Some((name, list)) => (
                        name.to_string(),
                        list.split(',').map(str::to_string).collect(),
                    ),
                    None => (value.clone(), default_members(value)),
                };
                if members.is_empty() {
                    bail!("{name} has no default member list; name them as --class {name}:member,member");
                }
                classes.push((name, members));
            }
            "--expect" => {
                let value = it.next().ok_or_else(|| anyhow!("--expect needs a value"))?;
                let (path, number) = value
                    .split_once('=')
                    .ok_or_else(|| anyhow!("--expect wants Class.member=value, got {value}"))?;
                let (class, member) = path
                    .split_once('.')
                    .ok_or_else(|| anyhow!("--expect wants Class.member=value, got {value}"))?;
                expect.push((class.to_string(), member.to_string(), number.parse()?));
            }
            "--dump" => {
                dump = Some(
                    it.next()
                        .ok_or_else(|| anyhow!("--dump needs a class"))?
                        .clone(),
                );
            }
            "--out" => {
                out = Some(
                    it.next()
                        .ok_or_else(|| anyhow!("--out needs a path"))?
                        .into(),
                );
            }
            other if other.starts_with("--") => bail!("unknown option {other}\n{USAGE}"),
            other => positional.push(other.to_string()),
        }
    }

    // `disasm` is its own mode: it takes none of the extract options.
    if positional.get(2).map(String::as_str) == Some("disasm") {
        let [dll, pdb, _, symbol] = positional.as_slice() else {
            bail!("disasm needs one symbol substring\n{USAGE}");
        };
        if !classes.is_empty() || !expect.is_empty() || dump.is_some() {
            bail!("disasm takes none of --class, --expect or --dump\n{USAGE}");
        }
        let out = out.ok_or_else(|| anyhow!("disasm needs --out <json>\n{USAGE}"))?;
        return Ok(Mode::Disasm(DisasmArgs {
            dll: dll.into(),
            pdb: pdb.into(),
            symbol: symbol.clone(),
            out,
        }));
    }

    if out.is_some() {
        bail!("--out belongs to the disasm mode; extract names its output positionally\n{USAGE}");
    }
    let [dll, pdb, out] = positional.as_slice() else {
        bail!("{USAGE}");
    };
    if classes.is_empty() {
        classes = DEFAULT_MEMBERS
            .iter()
            .map(|(class, members)| {
                (
                    class.to_string(),
                    members.iter().map(|m| m.to_string()).collect(),
                )
            })
            .collect();
    }
    Ok(Mode::Extract(Args {
        dll: dll.into(),
        pdb: pdb.into(),
        out: out.into(),
        classes,
        expect,
        dump,
    }))
}

fn default_members(class: &str) -> Vec<String> {
    DEFAULT_MEMBERS
        .iter()
        .find(|(name, _)| *name == class)
        .map(|(_, members)| members.iter().map(|m| m.to_string()).collect())
        .unwrap_or_default()
}

fn run(args: &Args) -> Result<()> {
    let pe = Pe::load(&args.dll)?;
    // `true` asks for the PDB's procedure lengths as well. They are what bounds
    // a leaf function MSVC gave no `.pdata` entry, and a constructor read only
    // as far as its first `ret` would miss every store after it -- so `extract`
    // pays the module-symbol walk (about 1.7 s) for the same three-source bound
    // `disasm` uses rather than guessing at an end.
    let index = PdbIndex::open(&args.pdb, &pe, true)?;

    // Every class in every requested chain gets each of its member functions
    // disassembled and traced once.
    let mut traced: Traced = HashMap::new();
    for (class, _) in &args.classes {
        for owner in index.chain(class) {
            if traced.contains_key(&owner) {
                continue;
            }
            let mut per_function = Vec::new();
            for function in index.functions.get(&owner).into_iter().flatten() {
                // The same bound, and the same stitching of a split function's
                // chained chunks, that `disasm` reports.
                let (chunks, bound) = disasm_chunks(&pe, &index.lengths, function.rva);
                let decoded: Vec<Vec<Instr>> = chunks
                    .iter()
                    .map(|chunk| decode_all(&chunk.bytes, chunk.rva))
                    .collect();
                // A chunk the decoder could not read to the end of is a cut no
                // byte-length check can see, and the bound has to say so.
                let bound = match undecoded(&chunks, &decoded) {
                    Some(why) => bound.truncated(why),
                    None => bound,
                };
                let instrs: Vec<Instr> = decoded.into_iter().flatten().collect();
                if Some(function.name.as_str()) == args.dump.as_deref()
                    || Some(owner.as_str()) == args.dump.as_deref()
                {
                    println!(
                        "; {} @ {:#x} ({} bytes from {}, {} chunks)",
                        function.name,
                        function.rva,
                        chunks.iter().map(|c| c.bytes.len()).sum::<usize>(),
                        bound.source,
                        chunks.len(),
                    );
                    for instr in &instrs {
                        println!("{:#010x}  {}", instr.rva, instr.text);
                    }
                }
                per_function.push((function.clone(), bound, trace_stores(&instrs, &pe)));
            }
            traced.insert(owner, per_function);
        }
    }

    let mut classes = BTreeMap::new();
    for (class, members) in &args.classes {
        classes.insert(class.clone(), resolve(class, members, &index, &traced)?);
    }

    let output = Output {
        provenance: Provenance {
            dll: file_name(&args.dll),
            pdb: file_name(&args.pdb),
            pdb_guid: index.guid.clone(),
            pdb_age: index.age,
            dll_sha256: pe.sha256.clone(),
            tool: concat!("sfy-native ", env!("CARGO_PKG_VERSION")).to_string(),
        },
        classes,
    };
    report(&output);
    // The oracle gates the write: an extraction the headers contradict never
    // replaces the committed one. Use --dump to see why it went wrong.
    check_oracle(&output, &args.expect)?;
    fs::write(&args.out, serde_json::to_string_pretty(&output)? + "\n")
        .with_context(|| format!("writing {}", args.out.display()))
}

fn file_name(path: &Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.display().to_string())
}

fn report(output: &Output) {
    for (class, data) in &output.classes {
        for (name, member) in &data.members {
            match &member.value {
                Some(value) => println!(
                    "{class}::{name} @ {} = {value} (set in {})",
                    member.offset,
                    member.set_in.as_deref().unwrap_or("?")
                ),
                None => println!(
                    "{class}::{name} @ {} = null -- {}",
                    member.offset,
                    member.reason.as_deref().unwrap_or("?")
                ),
            }
            for other in &member.also_set_in {
                println!("    also {} = {}", other.set_in, other.value);
            }
        }
    }
}

/// The oracle: values the public headers already state must come back exactly.
fn check_oracle(output: &Output, expect: &[(String, String, f64)]) -> Result<()> {
    let mut wrong = Vec::new();
    for (class, member, want) in expect {
        let got = output
            .classes
            .get(class)
            .and_then(|c| c.members.get(member))
            .and_then(|m| m.value.as_ref())
            .and_then(|v| v.as_f64());
        match got {
            Some(value) if (value - want).abs() <= 1e-3 * want.abs().max(1.0) => {
                println!("oracle ok: {class}::{member} = {value}");
            }
            Some(value) => wrong.push(format!("{class}::{member} is {value}, expected {want}")),
            None => wrong.push(format!("{class}::{member} has no value, expected {want}")),
        }
    }
    if wrong.is_empty() {
        Ok(())
    } else {
        bail!(
            "the oracle disagrees with the binary:\n  {}",
            wrong.join("\n  ")
        )
    }
}

fn main() -> Result<()> {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    match parse_args(&argv)? {
        Mode::Extract(args) => run(&args),
        Mode::Disasm(args) => run_disasm(&args),
    }
}

// ---------------------------------------------------------------------------
// Unit tests for the store tracer's codegen patterns
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    /// A Pe with one `.rdata` section holding `constants` at RVA 0x2000.
    fn fake_pe(constants: &[u8]) -> Pe {
        fake_pe_in(".rdata", constants)
    }

    /// The same, with the section named -- `.data` is not a constant pool.
    fn fake_pe_in(section: &str, constants: &[u8]) -> Pe {
        let mut bytes = vec![0u8; 0x1000];
        bytes.extend_from_slice(constants);
        bytes.resize(0x2000, 0);
        Pe {
            bytes,
            sections: vec![Section {
                name: section.to_string(),
                rva: 0x2000,
                virtual_size: 0x1000,
                raw_offset: 0x1000,
                raw_size: 0x1000,
            }],
            pdb_guid: String::new(),
            pdb_age: 0,
            pdb_name: String::new(),
            functions: BTreeMap::new(),
            chunks: BTreeMap::new(),
            chunk_owner: BTreeMap::new(),
            imports: BTreeMap::new(),
            sha256: String::new(),
        }
    }

    /// Decode `code` at RVA 0x1000, with the evidence formatter the tool uses.
    fn decode(code: &[u8]) -> Vec<Instr> {
        let mut decoder = Decoder::with_ip(64, code, 0x1000, DecoderOptions::NONE);
        let mut formatter = evidence_formatter();
        let mut out = Vec::new();
        let mut instruction = Instruction::default();
        while decoder.can_decode() {
            decoder.decode_out(&mut instruction);
            assert!(!instruction.is_invalid(), "bad hand-assembled bytes");
            let mut text = String::new();
            formatter.format(&instruction, &mut text);
            out.push(Instr {
                rva: instruction.ip() as u32,
                instruction,
                text,
            });
        }
        out
    }

    fn text_of(code: &[u8]) -> Vec<String> {
        decode(code).into_iter().map(|i| i.text).collect()
    }

    #[test]
    fn a_rip_relative_float_load_and_store_is_traced() {
        // mov rbx, rcx ; movss xmm0, [rip+0xff2] ; movss [rbx+0x3a8], xmm0
        let code = [
            0x48, 0x8b, 0xd9, 0xf3, 0x0f, 0x10, 0x05, 0xf5, 0x0f, 0x00, 0x00, 0xf3, 0x0f, 0x11,
            0x83, 0xa8, 0x03, 0x00, 0x00,
        ];
        assert_eq!(
            text_of(&code),
            [
                "mov rbx,rcx",
                "movss xmm0,dword ptr [2000h]",
                "movss dword ptr [rbx+3A8h],xmm0",
            ]
        );
        let pe = fake_pe(&200.0f32.to_le_bytes());
        let stores = trace_stores(&decode(&code), &pe);
        let (bytes, evidence) = stores.read(0x3a8, 4).expect("the store was not traced");
        assert_eq!(f32::from_le_bytes(bytes.try_into().unwrap()), 200.0);
        assert!(evidence.contains("movss xmm0"), "{evidence}");
        assert!(evidence.contains("rbx+3A8h"), "{evidence}");
    }

    #[test]
    fn xorps_stores_a_zero_float() {
        // xorps xmm1, xmm1 ; movss [rcx+0x10], xmm1
        let code = [0x0f, 0x57, 0xc9, 0xf3, 0x0f, 0x11, 0x49, 0x10];
        assert_eq!(
            text_of(&code),
            ["xorps xmm1,xmm1", "movss dword ptr [rcx+10h],xmm1"]
        );
        let stores = trace_stores(&decode(&code), &fake_pe(&[]));
        let (bytes, _) = stores.read(0x10, 4).expect("the zero store was not traced");
        assert_eq!(f32::from_le_bytes(bytes.try_into().unwrap()), 0.0);
    }

    #[test]
    fn a_merged_sixteen_byte_store_covers_four_floats() {
        // movups xmm2, [rip+0xff5] ; movups [rcx+0x20], xmm2
        let code = [
            0x0f, 0x10, 0x15, 0xf9, 0x0f, 0x00, 0x00, 0x0f, 0x11, 0x51, 0x20,
        ];
        assert_eq!(
            text_of(&code),
            [
                "movups xmm2,xmmword ptr [2000h]",
                "movups xmmword ptr [rcx+20h],xmm2"
            ]
        );
        let mut constants = Vec::new();
        for value in [1.0f32, 2.0, 3.0, 4.0] {
            constants.extend_from_slice(&value.to_le_bytes());
        }
        let stores = trace_stores(&decode(&code), &fake_pe(&constants));
        for (n, want) in [1.0f32, 2.0, 3.0, 4.0].iter().enumerate() {
            let (bytes, _) = stores.read(0x20 + 4 * n as u32, 4).expect("missing float");
            assert_eq!(f32::from_le_bytes(bytes.try_into().unwrap()), *want);
        }
    }

    #[test]
    fn an_eight_byte_immediate_store_covers_two_floats() {
        // mov rax, 0x43fa000043160000 ; mov [rcx+0x30], rax
        let code = [
            0x48, 0xb8, 0x00, 0x00, 0x16, 0x43, 0x00, 0x00, 0xfa, 0x43, 0x48, 0x89, 0x41, 0x30,
        ];
        assert_eq!(
            text_of(&code),
            ["mov rax,43FA000043160000h", "mov qword ptr [rcx+30h],rax"]
        );
        let stores = trace_stores(&decode(&code), &fake_pe(&[]));
        let (low, _) = stores.read(0x30, 4).unwrap();
        let (high, _) = stores.read(0x34, 4).unwrap();
        assert_eq!(f32::from_le_bytes(low.try_into().unwrap()), 150.0);
        assert_eq!(f32::from_le_bytes(high.try_into().unwrap()), 500.0);
    }

    #[test]
    fn an_immediate_dword_store_is_traced() {
        // mov dword ptr [rcx+0x40], 0x42c80000   (100.0f)
        let code = [0xc7, 0x41, 0x40, 0x00, 0x00, 0xc8, 0x42];
        assert_eq!(text_of(&code), ["mov dword ptr [rcx+40h],42C80000h"]);
        let stores = trace_stores(&decode(&code), &fake_pe(&[]));
        let (bytes, evidence) = stores.read(0x40, 4).unwrap();
        assert_eq!(f32::from_le_bytes(bytes.try_into().unwrap()), 100.0);
        assert!(evidence.contains("mov dword ptr"), "{evidence}");
    }

    #[test]
    fn a_call_drops_the_volatile_this_alias_but_keeps_the_saved_copy() {
        // mov rdi, rcx ; call $+5 ; movss xmm0,[rip+..] ; movss [rcx+8],xmm0
        // ; movss [rdi+4], xmm0
        let code = [
            0x48, 0x8b, 0xf9, 0xe8, 0x00, 0x00, 0x00, 0x00, 0xf3, 0x0f, 0x10, 0x05, 0xf0, 0x0f,
            0x00, 0x00, 0xf3, 0x0f, 0x11, 0x41, 0x08, 0xf3, 0x0f, 0x11, 0x47, 0x04,
        ];
        let pe = fake_pe(&7.5f32.to_le_bytes());
        let stores = trace_stores(&decode(&code), &pe);
        assert!(stores.read(0x08, 4).is_none(), "rcx survived a call");
        let (bytes, _) = stores.read(0x04, 4).expect("rdi did not survive the call");
        assert_eq!(f32::from_le_bytes(bytes.try_into().unwrap()), 7.5);
    }

    #[test]
    fn a_register_overwritten_between_load_and_store_is_not_trusted() {
        // movss xmm0,[rip+..] ; addss xmm0, xmm0 ; movss [rcx+0x50], xmm0
        let code = [
            0xf3, 0x0f, 0x10, 0x05, 0xf8, 0x0f, 0x00, 0x00, 0xf3, 0x0f, 0x58, 0xc0, 0xf3, 0x0f,
            0x11, 0x41, 0x50,
        ];
        let pe = fake_pe(&3.0f32.to_le_bytes());
        let stores = trace_stores(&decode(&code), &pe);
        assert!(
            stores.read(0x50, 4).is_none(),
            "a computed value was recorded"
        );
    }

    #[test]
    fn a_computed_store_vetoes_an_earlier_constant() {
        // mov dword ptr [rcx+0x70], 1 ; movss [rcx+0x70], xmm3
        let code = [
            0xc7, 0x41, 0x70, 0x01, 0x00, 0x00, 0x00, 0xf3, 0x0f, 0x11, 0x59, 0x70,
        ];
        assert_eq!(
            text_of(&code),
            [
                "mov dword ptr [rcx+70h],1",
                "movss dword ptr [rcx+70h],xmm3"
            ]
        );
        let stores = trace_stores(&decode(&code), &fake_pe(&[]));
        assert!(
            stores.read(0x70, 4).is_none(),
            "the constant outlived the store"
        );
        assert!(stores.computed_over(0x70, 4).is_some());
    }

    /// `mov [rbx+rax*4],x` writes a member the instruction does not name.
    ///
    /// The index is a run-time value, so the tracer cannot say which member the
    /// store lands on -- only that it lands on one. Every constant the function
    /// has recorded could be the one it overwrote, so all of them are vetoed.
    /// The same shape off a register that is not a `this` alias says nothing
    /// about this object and is ignored, as it always was.
    #[test]
    fn an_indexed_store_through_this_vetoes_the_constants_the_function_recorded() {
        // mov dword ptr [rcx+60h],2 ; mov dword ptr [rcx+70h],1
        // ; mov qword ptr [rcx+rax*8+80h],rdx
        let code = [
            0xc7, 0x41, 0x60, 0x02, 0x00, 0x00, 0x00, //
            0xc7, 0x41, 0x70, 0x01, 0x00, 0x00, 0x00, //
            0x48, 0x89, 0x94, 0xc1, 0x80, 0x00, 0x00, 0x00,
        ];
        assert_eq!(
            text_of(&code),
            [
                "mov dword ptr [rcx+60h],2",
                "mov dword ptr [rcx+70h],1",
                "mov qword ptr [rcx+rax*8+80h],rdx"
            ]
        );
        let stores = trace_stores(&decode(&code), &fake_pe(&[]));
        for offset in [0x60, 0x70] {
            assert!(
                stores.read(offset, 4).is_none(),
                "{offset:#x} outlived a store that could have hit it"
            );
            assert_eq!(
                stores.computed_over(offset, 4),
                Some("mov qword ptr [rcx+rax*8+80h],rdx @ 0x100e")
            );
        }

        // The same store off a base that is not `this` touches nothing here.
        // mov dword ptr [rcx+70h],1 ; mov qword ptr [rdx+rax*8+80h],rsi
        let elsewhere = [
            0xc7, 0x41, 0x70, 0x01, 0x00, 0x00, 0x00, //
            0x48, 0x89, 0xb4, 0xc2, 0x80, 0x00, 0x00, 0x00,
        ];
        assert_eq!(text_of(&elsewhere)[1], "mov qword ptr [rdx+rax*8+80h],rsi");
        let stores = trace_stores(&decode(&elsewhere), &fake_pe(&[]));
        assert_eq!(
            stores.read(0x70, 4).map(|(bytes, _)| bytes),
            Some(vec![1, 0, 0, 0])
        );
        assert_eq!(stores.computed_over(0x70, 4), None);
    }

    /// `lea rbx,[rcx+80h]` … `mov [rbx-10h],rsi` writes the object too.
    ///
    /// `rbx` is not an alias of `this` -- nothing is read through it -- so
    /// `store_target` never saw the store. It still overwrites a member, at an
    /// offset the `lea` and the displacement between them state exactly; the
    /// displacement is signed, and reading it unsigned would lose the offset.
    /// `AFGConveyorBeltHologram::ConfigureComponents` is the real one: it fills
    /// `mSnappedConnectionComponents` at 0x800 through a pointer `lea`'d to
    /// 0x810.
    #[test]
    fn a_store_through_a_pointer_leaed_off_this_is_vetoed_where_the_lea_stands() {
        // mov dword ptr [rcx+60h],2 ; mov dword ptr [rcx+70h],1
        // ; lea rbx,[rcx+80h] ; mov qword ptr [rbx-10h],rsi
        let code = [
            0xc7, 0x41, 0x60, 0x02, 0x00, 0x00, 0x00, //
            0xc7, 0x41, 0x70, 0x01, 0x00, 0x00, 0x00, //
            0x48, 0x8d, 0x99, 0x80, 0x00, 0x00, 0x00, //
            0x48, 0x89, 0x73, 0xf0,
        ];
        assert_eq!(
            text_of(&code)[2..],
            ["lea rbx,[rcx+80h]", "mov qword ptr [rbx-10h],rsi"]
        );
        let stores = trace_stores(&decode(&code), &fake_pe(&[]));
        assert!(stores.read(0x70, 4).is_none(), "0x80 - 0x10 is 0x70");
        assert_eq!(
            stores.computed_over(0x70, 4),
            Some("mov qword ptr [rbx-10h],rsi @ 0x1015")
        );
        // Placed, not wholesale: the constant it could not have reached stands.
        assert_eq!(
            stores.read(0x60, 4).map(|(bytes, _)| bytes),
            Some(vec![2, 0, 0, 0])
        );
        assert_eq!(stores.computed_over(0x60, 4), None);

        // A pointer with a run-time index in it cannot be placed, so it vetoes
        // every constant the function recorded instead.
        // mov dword ptr [rcx+60h],2 ; lea rbx,[rcx+rax*4+80h]
        // ; mov qword ptr [rbx],rsi
        let indexed = [
            0xc7, 0x41, 0x60, 0x02, 0x00, 0x00, 0x00, //
            0x48, 0x8d, 0x9c, 0x81, 0x80, 0x00, 0x00, 0x00, //
            0x48, 0x89, 0x33,
        ];
        assert_eq!(
            text_of(&indexed)[1..],
            ["lea rbx,[rcx+rax*4+80h]", "mov qword ptr [rbx],rsi"]
        );
        let stores = trace_stores(&decode(&indexed), &fake_pe(&[]));
        assert!(stores.read(0x60, 4).is_none());
        assert_eq!(
            stores.computed_over(0x60, 4),
            Some("mov qword ptr [rbx],rsi @ 0x100f")
        );
    }

    #[test]
    fn a_value_written_back_where_it_came_from_is_not_a_write() {
        // mov dword ptr [rcx+0x78], 1 ; movss xmm11,[rcx+0x78]
        // ; movss [rcx+0x78],xmm11
        let code = [
            0xc7, 0x41, 0x78, 0x01, 0x00, 0x00, 0x00, 0xf3, 0x44, 0x0f, 0x10, 0x59, 0x78, 0xf3,
            0x44, 0x0f, 0x11, 0x59, 0x78,
        ];
        assert_eq!(
            text_of(&code),
            [
                "mov dword ptr [rcx+78h],1",
                "movss xmm11,dword ptr [rcx+78h]",
                "movss dword ptr [rcx+78h],xmm11",
            ]
        );
        let stores = trace_stores(&decode(&code), &fake_pe(&[]));
        assert!(
            stores.computed_over(0x78, 4).is_none(),
            "a write-back vetoed it"
        );
        let (bytes, _) = stores.read(0x78, 4).unwrap();
        assert_eq!(u32::from_le_bytes(bytes.try_into().unwrap()), 1);
    }

    #[test]
    fn a_rip_relative_multiply_is_not_a_constant_load() {
        // movss xmm1,[rcx+0x80] ; mulss xmm1,[rip+..] ; movss [rcx+0x84],xmm1
        let code = [
            0xf3, 0x0f, 0x10, 0x89, 0x80, 0x00, 0x00, 0x00, 0xf3, 0x0f, 0x59, 0x0d, 0xf0, 0x0f,
            0x00, 0x00, 0xf3, 0x0f, 0x11, 0x89, 0x84, 0x00, 0x00, 0x00,
        ];
        assert_eq!(
            text_of(&code),
            [
                "movss xmm1,dword ptr [rcx+80h]",
                "mulss xmm1,dword ptr [2000h]",
                "movss dword ptr [rcx+84h],xmm1",
            ]
        );
        let stores = trace_stores(&decode(&code), &fake_pe(&24.0f32.to_le_bytes()));
        assert!(
            stores.read(0x84, 4).is_none(),
            "the multiplier was mistaken for the stored value"
        );
        assert!(stores.computed_over(0x84, 4).is_some());
    }

    #[test]
    fn a_later_store_wins_over_an_earlier_one() {
        // mov dword [rcx+0x60], 1 ; mov dword [rcx+0x60], 2
        let code = [
            0xc7, 0x41, 0x60, 0x01, 0x00, 0x00, 0x00, 0xc7, 0x41, 0x60, 0x02, 0x00, 0x00, 0x00,
        ];
        let stores = trace_stores(&decode(&code), &fake_pe(&[]));
        let (bytes, _) = stores.read(0x60, 4).unwrap();
        assert_eq!(u32::from_le_bytes(bytes.try_into().unwrap()), 2);
    }

    #[test]
    fn a_member_function_mangling_names_its_class() {
        assert_eq!(
            member_function("??0AFGConveyorBeltHologram@@QEAA@AEBVFObjectInitializer@@@Z"),
            Some((
                "AFGConveyorBeltHologram".to_string(),
                "AFGConveyorBeltHologram".to_string(),
                true
            ))
        );
        assert_eq!(
            member_function("?BeginPlay@AFGConveyorLiftHologram@@MEAAXXZ"),
            Some((
                "AFGConveyorLiftHologram".to_string(),
                "BeginPlay".to_string(),
                false
            ))
        );
        // Destructors, nested and templated scopes, and free functions.
        assert_eq!(member_function("??1AFGConveyorBeltHologram@@UEAA@XZ"), None);
        assert_eq!(member_function("??0Inner@Outer@@QEAA@XZ"), None);
        assert_eq!(member_function("?Get@?$TArray@M@@QEBAMXZ"), None);
        assert_eq!(member_function("?GlobalThing@@YAXXZ"), None);
    }

    #[test]
    fn a_codeview_guid_prints_the_way_the_pdb_names_it() {
        let raw: [u8; 16] = [
            0x7c, 0x1f, 0x69, 0xa2, 0x5e, 0xb4, 0x47, 0x79, 0x65, 0xc6, 0x53, 0x5d, 0xe3, 0x73,
            0xba, 0x04,
        ];
        assert_eq!(format_guid(&raw), "A2691F7C-B45E-7947-65C6-535DE373BA04");
    }

    #[test]
    fn a_float_member_decodes_from_its_four_bytes() {
        // The header says `mMaxSplineLength = 5600.1f`, and that is what has to
        // come back -- not the 5600.10009765625 the float widens to.
        assert_eq!(
            decode_value("float", &5600.1f32.to_le_bytes()),
            Some(serde_json::json!(5600.1))
        );
        assert_eq!(
            decode_value("float", &199.0f32.to_le_bytes()),
            Some(serde_json::json!(199.0))
        );
        assert_eq!(decode_value("int32", &7i32.to_le_bytes()), Some(7.into()));
        assert_eq!(decode_value("bool", &[1]), Some(true.into()));
        assert_eq!(decode_value("pointer", &[0; 8]), None);
    }

    // -----------------------------------------------------------------------
    // The disasm mode
    // -----------------------------------------------------------------------

    /// A Pe whose `.text` holds `code` at RVA 0x1000 and whose `.rdata` holds
    /// `constants` at RVA 0x2000.
    fn fake_module(code: &[u8], constants: &[u8]) -> Pe {
        let mut pe = fake_pe(constants);
        let mut bytes = vec![0u8; 0x400];
        bytes.extend_from_slice(code);
        bytes.resize(0x400 + 0x1000, 0);
        bytes.extend_from_slice(&pe.bytes[0x1000..]);
        pe.bytes = bytes;
        pe.sections.insert(
            0,
            Section {
                name: ".text".to_string(),
                rva: 0x1000,
                virtual_size: 0x1000,
                raw_offset: 0x400,
                raw_size: 0x1000,
            },
        );
        // `.rdata`'s bytes moved down by the `.text` the fake module grew.
        pe.sections[1].raw_offset = 0x1400;
        pe
    }

    /// `AFGConveyorBeltHologram : AFGBuildableHologram, IFGSaveInterface`:
    /// the members the annotation is checked against at the offsets the real
    /// PDB gives them, plus a secondary base the way a UE actor inherits its
    /// interfaces -- at a non-zero offset in the derived object.
    fn fake_index() -> PdbIndex {
        let float = |offset| Member {
            offset,
            type_name: "float".to_string(),
            size: 4,
        };
        let belt = Layout {
            size: 2400,
            base: Some("AFGBuildableHologram".to_string()),
            bases: vec![
                Base {
                    name: "AFGBuildableHologram".to_string(),
                    offset: 0,
                },
                Base {
                    name: "IFGSaveInterface".to_string(),
                    offset: 2300,
                },
            ],
            members: BTreeMap::from([
                ("mBendRadius".to_string(), float(2120)),
                ("mMaxSplineLength".to_string(), float(2124)),
            ]),
        };
        let base = Layout {
            size: 1300,
            base: None,
            bases: Vec::new(),
            members: BTreeMap::from([("mGridSnapSize".to_string(), float(1260))]),
        };
        let interface = Layout {
            size: 16,
            base: None,
            bases: Vec::new(),
            members: BTreeMap::from([("mSaveVersion".to_string(), float(8))]),
        };
        PdbIndex {
            guid: String::new(),
            age: 0,
            layouts: HashMap::from([
                ("AFGConveyorBeltHologram".to_string(), belt),
                ("AFGBuildableHologram".to_string(), base),
                ("IFGSaveInterface".to_string(), interface),
            ]),
            functions: HashMap::new(),
            symbols: Vec::new(),
            lengths: BTreeMap::new(),
        }
    }

    fn symbol(mangled: &str, rva: u32) -> Symbol {
        Symbol {
            rva,
            name: member_function(mangled)
                .map(|(class, function, _)| format!("{class}::{function}")),
            mangled: mangled.to_string(),
        }
    }

    #[test]
    fn a_read_through_this_names_the_member_and_the_class_that_owns_it() {
        // mov rbx,rcx ; movss xmm0,[rbx+0x848] ; movss xmm1,[rbx+0x4ec]
        let code = [
            0x48, 0x8b, 0xd9, 0xf3, 0x0f, 0x10, 0x83, 0x48, 0x08, 0x00, 0x00, 0xf3, 0x0f, 0x10,
            0x8b, 0xec, 0x04, 0x00, 0x00,
        ];
        assert_eq!(
            text_of(&code),
            [
                "mov rbx,rcx",
                "movss xmm0,dword ptr [rbx+848h]",
                "movss xmm1,dword ptr [rbx+4ECh]",
            ]
        );
        let pe = fake_module(&code, &[]);
        let spans = member_spans(&fake_index(), "AFGConveyorBeltHologram");
        let out = annotate(
            &pe,
            &code,
            0x1000,
            &decode(&code),
            &spans,
            &BTreeMap::new(),
            &mut Regs::with_this(Some(Register::RCX)),
        );
        assert!(out[0].member.is_none(), "{:?}", out[0].member);
        let bend = out[1].member.as_ref().expect("[rbx+848h] was not a member");
        assert_eq!(bend.name, "mBendRadius");
        assert_eq!(bend.class, "AFGConveyorBeltHologram");
        assert_eq!(bend.offset, 2120);
        assert_eq!(out[1].bytes, "f30f108348080000");
        // The base class's members are in the chain too.
        let snap = out[2].member.as_ref().expect("[rbx+4ECh] was not a member");
        assert_eq!(
            (snap.name.as_str(), snap.class.as_str()),
            ("mGridSnapSize", "AFGBuildableHologram")
        );
    }

    #[test]
    fn a_displacement_off_no_member_and_one_off_a_dead_alias_are_left_alone() {
        // movss xmm0,[rcx+0x848] ; call $+5 ; movss xmm0,[rcx+0x848]
        let code = [
            0xf3, 0x0f, 0x10, 0x81, 0x48, 0x08, 0x00, 0x00, 0xe8, 0x00, 0x00, 0x00, 0x00, 0xf3,
            0x0f, 0x10, 0x81, 0x48, 0x08, 0x00, 0x00,
        ];
        let pe = fake_module(&code, &[]);
        let spans = member_spans(&fake_index(), "AFGConveyorBeltHologram");
        let out = annotate(
            &pe,
            &code,
            0x1000,
            &decode(&code),
            &spans,
            &BTreeMap::new(),
            &mut Regs::with_this(Some(Register::RCX)),
        );
        assert_eq!(
            out[0].member.as_ref().map(|m| m.name.as_str()),
            Some("mBendRadius")
        );
        assert!(
            out[2].member.is_none(),
            "rcx still counted as `this` after a call"
        );
        // Nothing lives at 0x850 in the fake layout.
        let spans = member_spans(&fake_index(), "AFGConveyorBeltHologram");
        assert!(member_at(&spans, 0x850).is_none());
    }

    #[test]
    fn a_secondary_base_contributes_its_members_at_its_own_offset() {
        let spans = member_spans(&fake_index(), "AFGConveyorBeltHologram");
        let at = |displacement| {
            member_at(&spans, displacement).map(|s| (s.class.as_str(), s.name.as_str(), s.offset))
        };
        // The class's own members and the primary base's are where they were.
        assert_eq!(
            at(2120),
            Some(("AFGConveyorBeltHologram", "mBendRadius", 2120))
        );
        assert_eq!(
            at(1260),
            Some(("AFGBuildableHologram", "mGridSnapSize", 1260))
        );
        // The interface sits at 2300, so its member at 8 is at 2308 -- not at
        // 8, where the primary base's object header is.
        assert_eq!(at(2308), Some(("IFGSaveInterface", "mSaveVersion", 2308)));
        assert!(at(8).is_none());
    }

    #[test]
    fn the_mangling_says_whether_this_arrives_at_all() {
        // Plain instance members, virtual ones, and a constructor.
        for mangled in [
            "?ValidateCurvature@AFGConveyorBeltHologram@@AEAA_NXZ",
            "?BeginPlay@AFGConveyorLiftHologram@@MEAAXXZ",
            "??0AFGConveyorBeltHologram@@QEAA@AEBVFObjectInitializer@@@Z",
            "?GetWorld@AActor@@UEBAPEAVUWorld@@XZ",
            // Returning an object by value moves the caller's *return slot*
            // into `rdx`; `this` stays in `rcx`. Both of these are real
            // manglings from the module whose code says so.
            "?GetAnyConnectedBuildables@AFGConveyorBeltHologram@@QEAA?AV?$TArray@PEAVAFGBuildable@@V?$TSizedDefaultAllocator@$0CA@@@@@XZ",
            "?GetItemClass@FInventoryItem@@QEBA?AV?$TSubclassOf@VUFGItemDescriptor@@@@XZ",
        ] {
            assert_eq!(this_register(mangled), ThisIn::Rcx, "{mangled}");
        }
        // Static member functions -- public, protected and private -- have no
        // `this`, and a UE `exec` thunk's `rcx` is another class's `UObject*`.
        for mangled in [
            "?StaticClass@AFGConveyorBeltHologram@@SAPEAVUClass@@XZ",
            "?GetPrivateStaticClass@AFGConveyorBeltHologram@@CAPEAVUClass@@XZ",
            "?Helper@AFGHologram@@KAXXZ",
            "?execOnRep_ConnectionArrowComponentDirection@AFGConveyorBeltHologram@@SAXPEAVUObject@@AEAUFFrame@@QEAX@Z",
        ] {
            assert_eq!(this_register(mangled), ThisIn::None, "{mangled}");
        }
    }

    #[test]
    fn a_static_member_function_annotates_nothing_against_rcx() {
        // movss xmm0,[rcx+0x848] -- `rcx` is an argument here, not `this`.
        let code = [0xf3, 0x0f, 0x10, 0x81, 0x48, 0x08, 0x00, 0x00];
        let pe = fake_module(&code, &[]);
        let spans = member_spans(&fake_index(), "AFGConveyorBeltHologram");
        let out = annotate(
            &pe,
            &code,
            0x1000,
            &decode(&code),
            &spans,
            &BTreeMap::new(),
            &mut Regs::with_this(None),
        );
        assert!(out[0].member.is_none(), "{:?}", out[0].member);
    }

    #[test]
    fn an_sret_functions_return_slot_is_not_mistaken_for_this() {
        // movss xmm0,[rdx+0x848] ; movss xmm1,[rcx+0x848]
        let code = [
            0xf3, 0x0f, 0x10, 0x82, 0x48, 0x08, 0x00, 0x00, 0xf3, 0x0f, 0x10, 0x89, 0x48, 0x08,
            0x00, 0x00,
        ];
        assert_eq!(
            text_of(&code),
            [
                "movss xmm0,dword ptr [rdx+848h]",
                "movss xmm1,dword ptr [rcx+848h]",
            ]
        );
        let pe = fake_module(&code, &[]);
        let spans = member_spans(&fake_index(), "AFGConveyorBeltHologram");
        let out = annotate(
            &pe,
            &code,
            0x1000,
            &decode(&code),
            &spans,
            &BTreeMap::new(),
            &mut Regs::with_this(
                this_register("?GetAnyConnectedBuildables@AFGConveyorBeltHologram@@QEAA?AV?$TArray@PEAVAFGBuildable@@V?$TSizedDefaultAllocator@$0CA@@@@@XZ")
                    .register()
                    .map(|(_, register)| register),
            ),
        );
        assert!(
            out[0].member.is_none(),
            "the return slot was read as `this`: {:?}",
            out[0].member
        );
        assert_eq!(
            out[1].member.as_ref().map(|m| m.name.as_str()),
            Some("mBendRadius")
        );
    }

    #[test]
    fn a_call_rel32_is_resolved_against_the_symbol_map() {
        // call $+5 ; ret
        let code = [0xe8, 0x00, 0x00, 0x00, 0x00, 0xc3];
        assert_eq!(text_of(&code), ["call 0000000000001005h", "ret"]);
        let pe = fake_module(&code, &[]);
        let symbols = symbol_map(&[
            symbol("?GetSplineLength@USplineComponent@@QEBAMXZ", 0x1005),
            symbol("?Unnamed@?$TArray@M@@QEBAMXZ", 0x1005),
        ]);
        let out = annotate(
            &pe,
            &code,
            0x1000,
            &decode(&code),
            &[],
            &symbols,
            &mut Regs::with_this(Some(Register::RCX)),
        );
        assert_eq!(
            out[0].call.as_deref(),
            Some("USplineComponent::GetSplineLength")
        );
        assert!(out[1].call.is_none());
    }

    #[test]
    fn an_indirect_call_through_an_iat_slot_is_named_from_the_import_table() {
        // call qword ptr [rip+0xffa] -> 0x2000 ; mov rax,[rip+0xff3] -> 0x2000
        let code = [
            0xff, 0x15, 0xfa, 0x0f, 0x00, 0x00, 0x48, 0x8b, 0x05, 0xf3, 0x0f, 0x00, 0x00,
        ];
        assert_eq!(
            text_of(&code),
            ["call qword ptr [2000h]", "mov rax,qword ptr [2000h]"]
        );
        let mut pe = fake_module(&code, &[]);
        pe.imports.insert(
            0x2000,
            "?GetSplineLength@USplineComponent@@QEBAMXZ".to_string(),
        );
        let out = annotate(
            &pe,
            &code,
            0x1000,
            &decode(&code),
            &[],
            &BTreeMap::new(),
            &mut Regs::with_this(Some(Register::RCX)),
        );
        assert_eq!(
            out[0].import.as_deref(),
            Some("?GetSplineLength@USplineComponent@@QEBAMXZ")
        );
        // A `mov` off the same slot loads the pointer; it does not call it.
        assert!(out[1].import.is_none());
    }

    #[test]
    fn a_rip_relative_read_of_rdata_is_decoded_every_way() {
        // movss xmm0,[rip+0xff8]  -> 0x2000
        let code = [0xf3, 0x0f, 0x10, 0x05, 0xf8, 0x0f, 0x00, 0x00];
        assert_eq!(text_of(&code), ["movss xmm0,dword ptr [2000h]"]);
        let mut constants = 199.0f32.to_le_bytes().to_vec();
        constants.resize(16, 0);
        let pe = fake_module(&code, &constants);
        let out = annotate(
            &pe,
            &code,
            0x1000,
            &decode(&code),
            &[],
            &BTreeMap::new(),
            &mut Regs::with_this(Some(Register::RCX)),
        );
        let constant = out[0]
            .constant
            .as_ref()
            .expect("the .rdata read was missed");
        assert_eq!(constant.at, "0x2000");
        assert_eq!(constant.as_f32, Some(199.0));
        assert_eq!(constant.as_i32, Some(0x43470000));
        assert_eq!(constant.bytes, "00004743000000000000000000000000");
    }

    #[test]
    fn a_rip_relative_read_of_a_mutable_global_is_not_a_constant() {
        let code = [0xf3, 0x0f, 0x10, 0x05, 0xf8, 0x0f, 0x00, 0x00];
        let mut pe = fake_module(&code, &199.0f32.to_le_bytes());
        pe.sections[1].name = ".data".to_string();
        let out = annotate(
            &pe,
            &code,
            0x1000,
            &decode(&code),
            &[],
            &BTreeMap::new(),
            &mut Regs::with_this(Some(Register::RCX)),
        );
        assert!(out[0].constant.is_none(), "a `.data` global was reported");
    }

    #[test]
    fn pdata_gives_the_bounds_and_a_function_without_an_entry_falls_back_to_ret() {
        // nop ; nop ; ret ; nop
        let code = [0x90, 0x90, 0xc3, 0x90];
        let mut pe = fake_module(&code, &[]);

        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1000);
        assert_eq!((chunks[0].bytes.len(), bound.source), (3, "ret"));

        pe.index_pdata(&[RuntimeFunction {
            begin: 0x1000,
            end: 0x1004,
            unwind: 0x2000,
        }]);
        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1000);
        assert_eq!(
            (chunks.len(), chunks[0].bytes.len(), bound.source),
            (1, 4, "pdata")
        );
        assert_eq!(pe.pdata_bounds(0x1000), Some((0x1000, 0x1004)));
        // A chunk's symbol lands inside its entry, not on it.
        assert_eq!(pe.pdata_bounds(0x1002), Some((0x1000, 0x1004)));
        // The end is exclusive, and an RVA no entry covers has no bounds.
        assert_eq!(pe.pdata_bounds(0x1004), None);
        assert_eq!(pe.pdata_bounds(0x0800), None);
    }

    #[test]
    fn a_constructors_store_past_its_first_ret_is_traced_because_pdata_bounds_it() {
        // mov dword ptr [rcx+10h],41200000h ; ret
        // mov dword ptr [rcx+14h],42480000h ; ret
        //
        // The second store is the one `extract` used to miss: it bounded a
        // function at its first `ret` even inside a `.pdata` range, so anything
        // MSVC emitted after an early return -- or in a chained chunk -- was
        // never traced. This is the shape of that bug, in eighteen bytes.
        let code = [
            0xc7, 0x41, 0x10, 0x00, 0x00, 0x20, 0x41, // [rcx+10h] = 10.0f
            0xc3, // ret
            0xc7, 0x41, 0x14, 0x00, 0x00, 0x48, 0x42, // [rcx+14h] = 50.0f
            0xc3, // ret
        ];
        let mut pe = fake_module(&code, &[]);
        pe.index_pdata(&[RuntimeFunction {
            begin: 0x1000,
            end: 0x1000 + code.len() as u32,
            unwind: 0x2000,
        }]);

        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1000);
        assert_eq!(
            (chunks.len(), chunks[0].bytes.len(), bound.source),
            (1, 16, "pdata")
        );
        let instrs: Vec<Instr> = chunks
            .iter()
            .flat_map(|chunk| decode_all(&chunk.bytes, chunk.rva))
            .collect();
        assert_eq!(instrs.len(), 4, "the whole `.pdata` range has to decode");

        let stores = trace_stores(&instrs, &pe);
        let (first, _) = stores.read(0x10, 4).expect("the store before the ret");
        let (second, evidence) = stores.read(0x14, 4).expect("the store after the ret");
        assert_eq!(f32::from_le_bytes(first.try_into().unwrap()), 10.0);
        assert_eq!(f32::from_le_bytes(second.try_into().unwrap()), 50.0);
        assert_eq!(evidence, "mov dword ptr [rcx+14h],42480000h @ 0x1008");

        // Without the `.pdata` entry there is nothing to bound it and the
        // decoder stops at that first `ret`, which is exactly why a bound the
        // game states is what `extract` asks for.
        let unbounded = fake_module(&code, &[]);
        let (chunks, bound) = disasm_chunks(&unbounded, &BTreeMap::new(), 0x1000);
        assert_eq!((chunks[0].bytes.len(), bound.source), (8, "ret"));
    }

    /// `UNWIND_INFO`: version 1, `count` codes, optionally chained.
    fn unwind_info(chained: bool, count: u8, parent: Option<RuntimeFunction>) -> Vec<u8> {
        unwind_info_version(1, chained, count, parent)
    }

    /// The same, with the version field spelled out -- 1 and 2 are the two
    /// MSVC emits and the only two the tool reads.
    fn unwind_info_version(
        version: u8,
        chained: bool,
        count: u8,
        parent: Option<RuntimeFunction>,
    ) -> Vec<u8> {
        let flags = if chained { UNW_FLAG_CHAININFO } else { 0 };
        let mut out = vec![version | (flags << 3), 0, count, 0];
        // `count` 2-byte codes, padded to an even count.
        out.resize(4 + 2 * (count as usize + count as usize % 2), 0);
        if let Some(parent) = parent {
            out.extend_from_slice(&parent.begin.to_le_bytes());
            out.extend_from_slice(&parent.end.to_le_bytes());
            out.extend_from_slice(&parent.unwind.to_le_bytes());
        }
        out
    }

    #[test]
    fn chained_pdata_entries_are_one_function_in_rva_order() {
        // Three chunks of a split function, plus an unrelated neighbour.
        let primary = RuntimeFunction {
            begin: 0x1000,
            end: 0x1008,
            unwind: 0x2000,
        };
        let second = RuntimeFunction {
            begin: 0x1020,
            end: 0x1030,
            unwind: 0x2010,
        };
        let third = RuntimeFunction {
            begin: 0x1010,
            end: 0x1018,
            unwind: 0x2030,
        };
        let other = RuntimeFunction {
            begin: 0x1040,
            end: 0x1044,
            unwind: 0x2060,
        };

        let mut unwind = vec![0u8; 0x80];
        let mut put = |at: u32, bytes: Vec<u8>| {
            let at = (at - 0x2000) as usize;
            unwind[at..at + bytes.len()].copy_from_slice(&bytes);
        };
        put(0x2000, unwind_info(false, 0, None));
        put(0x2010, unwind_info(true, 0, Some(primary)));
        // One unwind code, so the parent sits past four bytes of padding --
        // the chunk two links down the chain, not one.
        put(0x2030, unwind_info(true, 1, Some(second)));
        put(0x2060, unwind_info(false, 0, None));

        let code = vec![0x90u8; 0x50];
        let mut pe = fake_module(&code, &unwind);
        // Deliberately not in `.pdata` order: the second chunk is indexed
        // before the primary it chains to, and the third before the second.
        pe.index_pdata(&[third, primary, second, other]);

        assert_eq!(
            pe.chunks.get(&0x1000),
            Some(&vec![(0x1000, 0x1008), (0x1010, 0x1018), (0x1020, 0x1030)])
        );
        assert_eq!(pe.chunks.get(&0x1040), Some(&vec![(0x1040, 0x1044)]));

        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1000);
        assert_eq!(bound.source, "pdata-chained");
        assert_eq!(
            chunks
                .iter()
                .map(|c| (c.rva, c.bytes.len()))
                .collect::<Vec<_>>(),
            [(0x1000, 8), (0x1010, 8), (0x1020, 16)]
        );

        // A function nothing chains to is still one chunk, and still `pdata`.
        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1040);
        assert_eq!(bound.source, "pdata");
        assert_eq!(bound.entry_offset, None);
        assert_eq!(chunks.len(), 1);
        assert_eq!((chunks[0].rva, chunks[0].bytes.len()), (0x1040, 4));
    }

    /// A symbol that is not an entry point still names a whole function.
    ///
    /// `.pdata` says which function an RVA belongs to whether the RVA is that
    /// function's entry point, a byte inside its first chunk, or the start of a
    /// chunk MSVC chained onto it. Each case decodes the same whole function
    /// from its own `begin`, and `entry_offset` is what tells the reader the
    /// name it was asked for sits partway in.
    #[test]
    fn a_symbol_inside_a_function_is_bounded_by_the_whole_function_it_is_part_of() {
        let primary = RuntimeFunction {
            begin: 0x1000,
            end: 0x1008,
            unwind: 0x2000,
        };
        let second = RuntimeFunction {
            begin: 0x1020,
            end: 0x1030,
            unwind: 0x2010,
        };
        let plain = RuntimeFunction {
            begin: 0x1040,
            end: 0x1050,
            unwind: 0x2060,
        };
        let mut unwind = vec![0u8; 0x80];
        let mut put = |at: u32, bytes: Vec<u8>| {
            let at = (at - 0x2000) as usize;
            unwind[at..at + bytes.len()].copy_from_slice(&bytes);
        };
        put(0x2000, unwind_info(false, 0, None));
        put(0x2010, unwind_info(true, 0, Some(primary)));
        put(0x2060, unwind_info(false, 0, None));

        let code = vec![0x90u8; 0x50];
        let mut pe = fake_module(&code, &unwind);
        pe.index_pdata(&[primary, second, plain]);

        let whole = [(0x1000u32, 8usize), (0x1020, 16)];
        let layout = |chunks: &[CodeChunk]| {
            chunks
                .iter()
                .map(|c| (c.rva, c.bytes.len()))
                .collect::<Vec<_>>()
        };

        // A byte inside the primary's own chunk: the function is the primary,
        // both its chunks are decoded, and the symbol is 4 bytes in.
        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1004);
        assert_eq!(bound.source, "pdata-chained");
        assert_eq!(bound.entry_offset, Some(4));
        assert_eq!(bound.reason, None);
        assert_eq!(layout(&chunks), whole);

        // The chained chunk's own begin: the same function, 0x20 bytes in --
        // not a 16-byte "function" starting at 0x1020.
        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1020);
        assert_eq!(bound.source, "pdata-chained");
        assert_eq!(bound.entry_offset, Some(0x20));
        assert_eq!(layout(&chunks), whole);

        // A byte inside an unsplit function reports that function, not the
        // tail of it, and `pdata` because it has one chunk.
        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1048);
        assert_eq!((bound.source, bound.entry_offset), ("pdata", Some(8)));
        assert_eq!(layout(&chunks), [(0x1040, 16)]);

        // An RVA in no entry at all still falls through to the PDB's length.
        let lengths = BTreeMap::from([(0x1060u32, 4u32)]);
        let (chunks, bound) = disasm_chunks(&pe, &lengths, 0x1060);
        assert_eq!(
            (bound.source, bound.entry_offset),
            ("pdb-procedure-length", None)
        );
        assert_eq!(layout(&chunks), [(0x1060, 4)]);
    }

    /// A chunk `.pdata` bounds but the decoder cannot read is `truncated`.
    ///
    /// The byte-length check sees a whole chunk; only the decode says that it
    /// stopped in the middle of it, so that is where the reason comes from.
    #[test]
    fn a_chunk_the_decoder_stops_inside_is_truncated_with_the_byte_that_stopped_it() {
        // `xor eax,eax` (2), `ff ff` -- which decodes as nothing -- then a
        // `ret` the decoder never reaches.
        let code = [0x33u8, 0xC0, 0xFF, 0xFF, 0xC3];
        let entry = RuntimeFunction {
            begin: 0x1000,
            end: 0x1005,
            unwind: 0x2000,
        };
        let mut pe = fake_module(&code, &unwind_info(false, 0, None));
        pe.index_pdata(&[entry]);

        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1000);
        assert_eq!((bound.source, chunks[0].bytes.len()), ("pdata", 5));

        let decoded = vec![decode_all(&chunks[0].bytes, chunks[0].rva)];
        assert_eq!(text_of(&code[..2]), ["xor eax,eax"]);
        assert_eq!(
            decoded[0]
                .iter()
                .map(|i| i.text.clone())
                .collect::<Vec<_>>(),
            ["xor eax,eax"]
        );

        let why = undecoded(&chunks, &decoded).expect("the decode stopped 2 bytes in");
        assert!(
            why.contains("decoding stopped 2 bytes into the 5-byte chunk at 0x1000")
                && why.contains("0x1002"),
            "{why}"
        );
        let cut = bound.truncated(why);
        assert_eq!(cut.source, "truncated");
        assert!(cut.reason.is_some());

        // A chunk the decoder reads to the end of says nothing.
        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1000);
        let whole = [0x33u8, 0xC0, 0xC3];
        let decoded = vec![decode_all(&whole, 0x1000)];
        assert_eq!(decoded[0].len(), 2);
        assert_eq!(
            undecoded(
                &[CodeChunk {
                    rva: 0x1000,
                    bytes: whole.to_vec()
                }],
                &decoded
            ),
            None
        );
        assert_eq!(bound.reason, None);
        drop(chunks);
    }

    /// `n` entries, each chaining to the next; the last one is a primary.
    ///
    /// Entry `i` covers `[0x1000 + 8i, 0x1008 + 8i)` and its `UNWIND_INFO` sits
    /// at `0x2000 + 0x10 * i`, which is room enough for a header and a parent.
    fn chain_of(n: usize, cyclic: bool) -> (Pe, Vec<RuntimeFunction>) {
        let entry = |i: usize| RuntimeFunction {
            begin: 0x1000 + 8 * i as u32,
            end: 0x1008 + 8 * i as u32,
            unwind: 0x2000 + 0x10 * i as u32,
        };
        let entries: Vec<RuntimeFunction> = (0..n).map(entry).collect();
        let mut unwind = vec![0u8; 0x10 * n];
        for i in 0..n {
            // The last link chains back to the first when `cyclic`, and is a
            // primary otherwise.
            let parent = match (i + 1 < n, cyclic) {
                (true, _) => Some(entries[i + 1]),
                (false, true) => Some(entries[0]),
                (false, false) => None,
            };
            let bytes = unwind_info(parent.is_some(), 0, parent);
            unwind[0x10 * i..0x10 * i + bytes.len()].copy_from_slice(&bytes);
        }
        (fake_module(&vec![0x90u8; 0x1000], &unwind), entries)
    }

    #[test]
    fn an_unwind_chain_that_loops_or_runs_long_stops_instead_of_spinning() {
        // A cycle: two entries, each naming the other as its parent. Without
        // the seen-set this never returns.
        // Each walks the other and comes back to where it started, which is
        // the conservative answer: its own begin, and no chunk stolen.
        let (pe, entries) = chain_of(2, true);
        assert_eq!(pe.primary_of(entries[0]), entries[0].begin);
        assert_eq!(pe.primary_of(entries[1]), entries[1].begin);

        // A chain longer than MAX_CHAIN_DEPTH stops at that many hops rather
        // than walking a `.pdata` a hostile or corrupt module made arbitrarily
        // deep. The answer is wrong -- it is not the root -- but it is an
        // address in the module and the run finishes.
        let depth = MAX_CHAIN_DEPTH + 4;
        let (pe, entries) = chain_of(depth, false);
        assert_eq!(pe.primary_of(entries[0]), entries[MAX_CHAIN_DEPTH].begin);
        // Everything within reach of the end still resolves to the real root.
        assert_eq!(pe.primary_of(entries[depth - 1]), entries[depth - 1].begin);
        assert_eq!(pe.primary_of(entries[4]), entries[depth - 1].begin);

        // And the grouping that comes out of it terminates too: the deep chain
        // does not put every chunk under one primary.
        let mut pe = pe;
        pe.index_pdata(&entries);
        assert!(pe.chunks.contains_key(&entries[MAX_CHAIN_DEPTH].begin));
        assert!(pe.chunks.contains_key(&entries[depth - 1].begin));
    }

    #[test]
    fn an_unwind_info_version_the_tool_does_not_know_chains_to_nothing() {
        let parent = RuntimeFunction {
            begin: 0x1000,
            end: 0x1008,
            unwind: 0x2000,
        };
        let child = RuntimeFunction {
            begin: 0x1010,
            end: 0x1018,
            unwind: 0x2010,
        };
        let build = |version: u8| {
            let mut unwind = vec![0u8; 0x40];
            let head = unwind_info(false, 0, None);
            unwind[..head.len()].copy_from_slice(&head);
            let chained = unwind_info_version(version, true, 0, Some(parent));
            unwind[0x10..0x10 + chained.len()].copy_from_slice(&chained);
            fake_module(&[0x90u8; 0x40], &unwind)
        };

        // The two MSVC emits. Version 2 is the epilogue-annotated variant and
        // has the same header layout, so refusing it would silently drop chunks.
        for version in [1u8, 2] {
            let pe = build(version);
            assert_eq!(pe.chained_parent(child.unwind), Some(parent), "v{version}");
        }
        // Anything else is an UNWIND_INFO this tool has not read the layout of,
        // so the entry is its own primary rather than a parse of unknown bytes.
        for version in [0u8, 3, 4, 7] {
            let pe = build(version);
            assert_eq!(pe.chained_parent(child.unwind), None, "v{version}");
            let mut pe = pe;
            pe.index_pdata(&[parent, child]);
            assert_eq!(pe.chunks.get(&child.begin), Some(&vec![(0x1010, 0x1018)]));
            assert_eq!(pe.chunks.get(&parent.begin), Some(&vec![(0x1000, 0x1008)]));
        }
    }

    #[test]
    fn a_leaf_without_a_pdata_entry_is_bounded_by_the_pdbs_procedure_length() {
        // ret ; nop ; nop ; ret  -- the first `ret` is not the last one, which
        // is exactly the shape that loses AFGBuildableHologram::GetRotationStep
        // its 90/45/0 branches.
        let code = [0xc3, 0x90, 0x90, 0xc3];
        let pe = fake_module(&code, &[]);
        let lengths = BTreeMap::from([(0x1000u32, 4u32)]);

        // With no `.pdata` entry and no PDB length, the first `ret` is all
        // there is, and the tool says so.
        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1000);
        assert_eq!((chunks[0].bytes.len(), bound.source), (1, "ret"));

        // The PDB states the length, so the whole function decodes.
        let (chunks, bound) = disasm_chunks(&pe, &lengths, 0x1000);
        assert_eq!(
            (chunks.len(), chunks[0].bytes.len(), bound.source),
            (1, 4, "pdb-procedure-length")
        );
        assert_eq!(decode_all(&chunks[0].bytes, 0x1000).len(), 4);

        // `.pdata` still wins: a function it covers never consults the PDB.
        let mut covered = fake_module(&code, &[]);
        covered.index_pdata(&[RuntimeFunction {
            begin: 0x1000,
            end: 0x1002,
            unwind: 0x2000,
        }]);
        let (chunks, bound) = disasm_chunks(&covered, &lengths, 0x1000);
        assert_eq!((chunks[0].bytes.len(), bound.source), (2, "pdata"));

        // A stated length the section cannot satisfy is not passed off as one.
        let (_, bound) = disasm_chunks(&pe, &BTreeMap::from([(0x1000u32, 0x9000u32)]), 0x1000);
        assert_eq!(bound.source, "truncated");
    }

    #[test]
    fn a_function_with_no_ret_in_range_says_it_was_truncated() {
        let code = [0x90, 0x90, 0x90, 0x90];
        let mut pe = fake_module(&code, &[]);
        let (_, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1000);
        assert_eq!(bound.source, "truncated");

        // A `.pdata` range that runs past the section is not the function it
        // promised either, and must not pass for one.
        pe.index_pdata(&[RuntimeFunction {
            begin: 0x1000,
            end: 0x9000,
            unwind: 0x2000,
        }]);
        let (chunks, bound) = disasm_chunks(&pe, &BTreeMap::new(), 0x1000);
        assert_eq!((chunks[0].bytes.len(), bound.source), (0x1000, "truncated"));
    }

    #[test]
    fn the_substring_match_is_case_sensitive_and_over_the_class_method_form() {
        let symbols = vec![
            symbol("?ValidateCurvature@AFGConveyorBeltHologram@@AEAA_NXZ", 0x30),
            symbol("?ValidateCurvature@AFGPipelineHologram@@AEAA_NXZ", 0x20),
            // Folded onto one address: one function, reported once.
            symbol("?Serialize@FInventoryItem@@QEAA_NAEAVFArchive@@@Z", 0x10),
            symbol("?Serialize@FItemAmount@@QEAA_NAEAVFArchive@@@Z", 0x10),
            // No Class::Method name, so nothing to match on.
            symbol("?Get@?$TArray@M@@QEBAMXZ", 0x40),
        ];
        let names = |needle: &str| {
            find_functions(&symbols, needle)
                .into_iter()
                .map(|s| s.display().to_string())
                .collect::<Vec<_>>()
        };
        assert_eq!(
            names("AFGConveyorBeltHologram::ValidateCurvature"),
            ["AFGConveyorBeltHologram::ValidateCurvature"]
        );
        // Sorted by RVA, and one entry per address.
        assert_eq!(
            names("ValidateCurvature"),
            [
                "AFGPipelineHologram::ValidateCurvature",
                "AFGConveyorBeltHologram::ValidateCurvature",
            ]
        );
        assert_eq!(names("Serialize"), ["FInventoryItem::Serialize"]);
        assert!(names("validatecurvature").is_empty());
        assert!(names("TArray").is_empty());
    }

    #[test]
    fn the_disasm_mode_is_its_own_command_line() {
        let argv = |args: &[&str]| args.iter().map(|a| a.to_string()).collect::<Vec<_>>();
        let Ok(Mode::Disasm(args)) = parse_args(&argv(&[
            "game.dll",
            "game.pdb",
            "disasm",
            "AFGConveyorBeltHologram::ValidateCurvature",
            "--out",
            "vc.json",
        ])) else {
            panic!("disasm was not parsed");
        };
        assert_eq!(args.symbol, "AFGConveyorBeltHologram::ValidateCurvature");
        assert_eq!(args.out, PathBuf::from("vc.json"));

        // The extract mode is untouched, and the two do not mix.
        let Ok(Mode::Extract(args)) = parse_args(&argv(&["game.dll", "game.pdb", "native.json"]))
        else {
            panic!("extract was not parsed");
        };
        assert_eq!(args.out, PathBuf::from("native.json"));
        assert!(!args.classes.is_empty());
        assert!(parse_args(&argv(&["game.dll", "game.pdb", "disasm", "X"])).is_err());
        assert!(parse_args(&argv(&["game.dll", "game.pdb", "out.json", "--out", "x"])).is_err());
        assert!(parse_args(&argv(&[
            "game.dll", "game.pdb", "disasm", "X", "--out", "x", "--dump", "C"
        ]))
        .is_err());
    }
}
