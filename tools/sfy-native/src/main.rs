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

/// The shipped DLL: its sections, its CodeView debug record and its `.pdata`.
struct Pe {
    bytes: Vec<u8>,
    sections: Vec<Section>,
    pdb_guid: String,
    pdb_age: u32,
    pdb_name: String,
    /// `.pdata` RUNTIME_FUNCTION entries as (begin rva, end rva).
    functions: BTreeMap<u32, u32>,
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

        let mut pe = Pe {
            bytes,
            sections,
            pdb_guid,
            pdb_age,
            pdb_name,
            functions: BTreeMap::new(),
            sha256: String::new(),
        };
        pe.sha256 = hex(&Sha256::digest(&pe.bytes));
        pe.functions = pe.parse_pdata(exception_dir.virtual_address, exception_dir.size)?;
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
    fn parse_pdata(&self, rva: u32, size: u32) -> Result<BTreeMap<u32, u32>> {
        let table = self
            .rva_to_bytes(rva, size as usize)
            .ok_or_else(|| anyhow!("exception directory at {rva:#x} is outside every section"))?;
        let mut functions = BTreeMap::new();
        for entry in table.as_chunks::<12>().0 {
            let begin = u32::from_le_bytes(entry[0..4].try_into().unwrap());
            let end = u32::from_le_bytes(entry[4..8].try_into().unwrap());
            if end > begin {
                functions.insert(begin, end);
            }
        }
        Ok(functions)
    }

    /// The `[begin, end)` of the function starting at `rva`, `.pdata` first.
    fn function_range(&self, rva: u32) -> (u32, u32) {
        match self.functions.get(&rva) {
            Some(end) => (rva, *end),
            None => (rva, rva.saturating_add(0x1_0000)),
        }
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

/// One class: its size, its first base class, and its data members by name.
#[derive(Clone, Debug, Default)]
struct Layout {
    size: u64,
    base: Option<String>,
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
        let (functions, symbols) = read_functions(&mut pdb, symbols)?;
        Ok(PdbIndex {
            guid,
            age: info.age,
            layouts,
            functions,
            symbols,
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
                    pdb::TypeData::BaseClass(base) if layout.base.is_none() => {
                        if let Some(base_name) = type_name_of(&finder, base.base_class) {
                            layout.base = Some(base_name);
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

/// Decode the function at `rva` until its `.pdata` end, or its first `ret`.
fn disassemble(pe: &Pe, rva: u32) -> Vec<Instr> {
    const CAP: usize = 0x1_0000;
    let (begin, end) = pe.function_range(rva);
    let wanted = (end - begin) as usize;
    let Some(bytes) = pe.rva_to_bytes(begin, wanted.min(CAP)) else {
        return Vec::new();
    };
    let mut decoder = Decoder::with_ip(64, bytes, begin as u64, DecoderOptions::NONE);
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
        let rva = instruction.ip() as u32;
        let done =
            instruction.mnemonic() == Mnemonic::Ret || instruction.mnemonic() == Mnemonic::Int3;
        instrs.push(Instr {
            rva,
            instruction,
            text,
        });
        if done {
            break;
        }
    }
    instrs
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

/// Every traced function of a class, by the class it is a member of.
type Traced = HashMap<String, Vec<(Function, Stores)>>;

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
    info_factory: InstructionInfoFactory,
}

impl Regs {
    /// At the entry of a member function `this` is in `rcx` and nothing else
    /// is known.
    fn new() -> Regs {
        Regs {
            aliases: HashSet::from([Register::RCX]),
            loaded: HashMap::new(),
            reloaded: HashMap::new(),
            info_factory: InstructionInfoFactory::new(),
        }
    }

    /// Carry the register state across one instruction.
    fn step(&mut self, instr: &Instr, pe: &Pe) {
        let i = &instr.instruction;

        // A new alias, a new constant, a reload -- or none of the three.
        let kept = match register_effect(instr, pe, &self.aliases) {
            Some(Effect::Alias(register)) => {
                self.aliases.insert(register);
                self.loaded.remove(&register);
                self.reloaded.remove(&register);
                Some(register)
            }
            Some(Effect::Constant(register, value)) => {
                self.aliases.remove(&register);
                self.reloaded.remove(&register);
                self.loaded.insert(register, value);
                Some(register)
            }
            Some(Effect::Reload(register, offset, size)) => {
                self.aliases.remove(&register);
                self.loaded.remove(&register);
                self.reloaded.insert(register, (offset, size));
                Some(register)
            }
            None => None,
        };

        if i.flow_control() == FlowControl::Call || i.flow_control() == FlowControl::IndirectCall {
            for register in VOLATILE {
                self.aliases.remove(register);
                self.loaded.remove(register);
                self.reloaded.remove(register);
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
        }
    }
}

/// Follow `this` and the constants loaded for it through one constructor.
///
/// `this` arrives in `rcx`; MSVC usually copies it into a callee-saved register
/// before calling the base constructor, so the alias set grows on
/// `mov reg, alias` and shrinks whenever a register is written otherwise. A
/// store through any alias is recorded at its displacement.
fn trace_stores(instrs: &[Instr], pe: &Pe) -> Stores {
    let mut stores = Stores::default();
    let mut regs = Regs::new();

    for instr in instrs {
        let i = &instr.instruction;

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
}

fn register_effect(instr: &Instr, pe: &Pe, aliases: &HashSet<Register>) -> Option<Effect> {
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
        for (function, stores) in traced.get(owner).into_iter().flatten() {
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
    /// `pdata` when `.pdata` gave the bounds, `ret` when the first `ret` did,
    /// `truncated` when neither did -- never a silent cut.
    size_source: &'static str,
    instructions: Vec<InstrOut>,
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
    for owner in index.chain(class) {
        let Some(layout) = index.layouts.get(&owner) else {
            continue;
        };
        for (name, member) in &layout.members {
            spans.push(MemberSpan {
                class: owner.clone(),
                name: name.clone(),
                offset: member.offset,
                size: member.size,
            });
        }
    }
    spans
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

/// The `[begin, end)` to decode and where those bounds came from.
///
/// `.pdata` is authoritative. A function with no entry falls back to the
/// first `ret`, and one that runs past the cap without a `ret` says so.
fn disasm_bounds(pe: &Pe, rva: u32) -> (Vec<u8>, &'static str) {
    const CAP: usize = 0x1_0000;
    if let Some((_, end)) = pe.pdata_bounds(rva) {
        let wanted = end.saturating_sub(rva) as usize;
        if let Some(bytes) = pe.rva_to_bytes(rva, wanted.min(CAP)) {
            // A range the section or the cap cuts short is not the function
            // `.pdata` promised, and says so rather than passing for one.
            let source = if bytes.len() == wanted {
                "pdata"
            } else {
                "truncated"
            };
            return (bytes.to_vec(), source);
        }
    }
    let Some(bytes) = pe.rva_to_bytes(rva, CAP) else {
        return (Vec::new(), "truncated");
    };
    // No `.pdata` entry: decode to the first `ret` and keep it.
    let mut decoder = Decoder::with_ip(64, bytes, rva as u64, DecoderOptions::NONE);
    let mut instruction = Instruction::default();
    while decoder.can_decode() {
        decoder.decode_out(&mut instruction);
        if instruction.is_invalid() {
            break;
        }
        if instruction.mnemonic() == Mnemonic::Ret {
            let end = (instruction.ip() as u32 - rva) as usize + instruction.len();
            return (bytes[..end.min(bytes.len())].to_vec(), "ret");
        }
    }
    (bytes.to_vec(), "truncated")
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
/// register is written to otherwise or a call clobbers it.
fn annotate(
    pe: &Pe,
    buffer: &[u8],
    begin: u32,
    instrs: &[Instr],
    spans: &[MemberSpan],
    symbols: &BTreeMap<u32, String>,
) -> Vec<InstrOut> {
    let mut regs = Regs::new();
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
    let (buffer, size_source) = disasm_bounds(pe, symbol.rva);
    let instrs = decode_all(&buffer, symbol.rva);
    let spans = symbol
        .class()
        .map(|class| member_spans(index, class))
        .unwrap_or_default();
    DisasmOut {
        symbol: symbol.display().to_string(),
        mangled: symbol.mangled.clone(),
        rva: format!("{:#x}", symbol.rva),
        size: buffer.len() as u32,
        size_source,
        instructions: annotate(pe, &buffer, symbol.rva, &instrs, &spans, symbols),
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
    let index = PdbIndex::open(&args.pdb, &pe, false)?;

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
                let instrs = disassemble(&pe, function.rva);
                if Some(function.name.as_str()) == args.dump.as_deref()
                    || Some(owner.as_str()) == args.dump.as_deref()
                {
                    println!("; {} @ {:#x}", function.name, function.rva);
                    for instr in &instrs {
                        println!("{:#010x}  {}", instr.rva, instr.text);
                    }
                }
                per_function.push((function.clone(), trace_stores(&instrs, &pe)));
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
            sha256: String::new(),
        }
    }

    /// Decode `code` at RVA 0x1000 the way `disassemble` would.
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

    /// `AFGConveyorBeltHologram : AFGBuildableHologram`, the two members the
    /// annotation is checked against at the offsets the real PDB gives them.
    fn fake_index() -> PdbIndex {
        let float = |offset| Member {
            offset,
            type_name: "float".to_string(),
            size: 4,
        };
        let belt = Layout {
            size: 2400,
            base: Some("AFGBuildableHologram".to_string()),
            members: BTreeMap::from([
                ("mBendRadius".to_string(), float(2120)),
                ("mMaxSplineLength".to_string(), float(2124)),
            ]),
        };
        let base = Layout {
            size: 1300,
            base: None,
            members: BTreeMap::from([("mGridSnapSize".to_string(), float(1260))]),
        };
        PdbIndex {
            guid: String::new(),
            age: 0,
            layouts: HashMap::from([
                ("AFGConveyorBeltHologram".to_string(), belt),
                ("AFGBuildableHologram".to_string(), base),
            ]),
            functions: HashMap::new(),
            symbols: Vec::new(),
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
        let out = annotate(&pe, &code, 0x1000, &decode(&code), &spans, &BTreeMap::new());
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
        let out = annotate(&pe, &code, 0x1000, &decode(&code), &spans, &BTreeMap::new());
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
    fn a_call_rel32_is_resolved_against_the_symbol_map() {
        // call $+5 ; ret
        let code = [0xe8, 0x00, 0x00, 0x00, 0x00, 0xc3];
        assert_eq!(text_of(&code), ["call 0000000000001005h", "ret"]);
        let pe = fake_module(&code, &[]);
        let symbols = symbol_map(&[
            symbol("?GetSplineLength@USplineComponent@@QEBAMXZ", 0x1005),
            symbol("?Unnamed@?$TArray@M@@QEBAMXZ", 0x1005),
        ]);
        let out = annotate(&pe, &code, 0x1000, &decode(&code), &[], &symbols);
        assert_eq!(
            out[0].call.as_deref(),
            Some("USplineComponent::GetSplineLength")
        );
        assert!(out[1].call.is_none());
    }

    #[test]
    fn a_rip_relative_read_of_rdata_is_decoded_every_way() {
        // movss xmm0,[rip+0xff8]  -> 0x2000
        let code = [0xf3, 0x0f, 0x10, 0x05, 0xf8, 0x0f, 0x00, 0x00];
        assert_eq!(text_of(&code), ["movss xmm0,dword ptr [2000h]"]);
        let mut constants = 199.0f32.to_le_bytes().to_vec();
        constants.resize(16, 0);
        let pe = fake_module(&code, &constants);
        let out = annotate(&pe, &code, 0x1000, &decode(&code), &[], &BTreeMap::new());
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
        let out = annotate(&pe, &code, 0x1000, &decode(&code), &[], &BTreeMap::new());
        assert!(out[0].constant.is_none(), "a `.data` global was reported");
    }

    #[test]
    fn pdata_gives_the_bounds_and_a_function_without_an_entry_falls_back_to_ret() {
        // nop ; nop ; ret ; nop
        let code = [0x90, 0x90, 0xc3, 0x90];
        let mut pe = fake_module(&code, &[]);

        let (buffer, source) = disasm_bounds(&pe, 0x1000);
        assert_eq!((buffer.len(), source), (3, "ret"));

        pe.functions.insert(0x1000, 0x1004);
        let (buffer, source) = disasm_bounds(&pe, 0x1000);
        assert_eq!((buffer.len(), source), (4, "pdata"));
        assert_eq!(pe.pdata_bounds(0x1000), Some((0x1000, 0x1004)));
        // A chunk's symbol lands inside its entry, not on it.
        assert_eq!(pe.pdata_bounds(0x1002), Some((0x1000, 0x1004)));
        // The end is exclusive, and an RVA no entry covers has no bounds.
        assert_eq!(pe.pdata_bounds(0x1004), None);
        assert_eq!(pe.pdata_bounds(0x0800), None);
    }

    #[test]
    fn a_function_with_no_ret_in_range_says_it_was_truncated() {
        let code = [0x90, 0x90, 0x90, 0x90];
        let mut pe = fake_module(&code, &[]);
        let (_, source) = disasm_bounds(&pe, 0x1000);
        assert_eq!(source, "truncated");

        // A `.pdata` range that runs past the section is not the function it
        // promised either, and must not pass for one.
        pe.functions.insert(0x1000, 0x9000);
        let (buffer, source) = disasm_bounds(&pe, 0x1000);
        assert_eq!((buffer.len(), source), (0x1000, "truncated"));
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
