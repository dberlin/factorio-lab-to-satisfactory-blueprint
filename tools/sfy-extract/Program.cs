using System.Globalization;
using System.Security.Cryptography;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider.Usmap;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Objects.Core.Math;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using Newtonsoft.Json;
using SfyExtract;

// Usage: dotnet run -- <SatisfactoryDir> <mode> [filter|out.json]
//   list    [filter] : every Build_* package and its export names/classes (discovery)
//   props   [filter] : the same, plus every property of every export (discovery)
//   extract [out]    : write assets.json
//
// `filter` is a case-insensitive substring of the package path. With a filter,
// packages that are not named Build_* are searched too, which is how you look at
// a Holo_* hologram class.
if (args.Length < 2)
{
    Console.Error.WriteLine("usage: dotnet run -- <SatisfactoryDir> <list|props|extract> [filter|out.json]");
    return 2;
}

var gameDir = args[0];
var mode = args[1];
var arg2 = args.Length > 2 ? args[2] : "";
var filter = mode == "extract" ? "" : arg2;
var paks = Path.Combine(gameDir, "FactoryGame", "Content", "Paks");
var usmap = Path.Combine(gameDir, "CommunityResources", "FactoryGame.usmap");

var mappings = UsmapCompat.Patch(usmap, out var patchedTypes);
Console.Error.WriteLine($"usmap {usmap} -> {mappings} ({patchedTypes} OptionalProperty leaves patched)");

var provider = new DefaultFileProvider(
    paks, SearchOption.AllDirectories, new VersionContainer(EGame.GAME_UE5_6), StringComparer.OrdinalIgnoreCase);
provider.MappingsContainer = new FileUsmapTypeMappingsProvider(mappings);
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

var ports = new SortedDictionary<string, List<object>>(StringComparer.Ordinal);
var holograms = new SortedDictionary<string, Dictionary<string, object?>>(StringComparer.Ordinal);
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

    var cdo = exports.FirstOrDefault(e => e.Name == "Default__" + className);
    if (cdo is null) continue;
    var hologram = HologramLimits(cdo);
    if (hologram is not null) holograms[className] = hologram;
}

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
    },
    ports,
    holograms,
    wires = WireLengths(gameDir),
    grid = new Dictionary<string, object?>
    {
        // No cooked asset carries AFGBuildableHologram::mGridSnapSize's default;
        // tools/sfy-native reads it out of the shipped DLL (100). The
        // per-hologram overrides that do exist (Holo_PowerPole, Holo_StreetLight,
        // both 50) are in `holograms`, and the merge puts them on the buildable.
        ["mGridSnapSize"] = null,
        // Not a game value: 90 degrees is the step the build gun rotates by, and
        // it is in no asset, no header and no constructor immediate. Recorded
        // here so the merge has one place to find it, and tagged "constant" in
        // registry.json's limits_sources so it is never read as game data.
        ["rotation_step"] = 90,
    },
};

var outPath = arg2.Length > 0 ? arg2 : "assets.json";
File.WriteAllText(outPath, JsonConvert.SerializeObject(output, Formatting.Indented) + "\n");
Console.Error.WriteLine($"wrote {outPath}: {ports.Count} classes with ports, {holograms.Count} with a hologram class");
Console.Error.WriteLine($"connection classes left out on purpose: {string.Join(", ", unmodelled)}");
return 0;

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
List<object> CollectPorts(UObject generatedClass, List<UObject> exports)
{
    var seen = new HashSet<string>(StringComparer.Ordinal);
    var collected = new List<object>();
    var current = generatedClass;
    var currentExports = exports;
    for (var depth = 0; depth < 16 && current is not null; depth++)
    {
        foreach (var template in currentExports.Where(IsConnectionTemplate).OrderBy(e => e.Name, StringComparer.Ordinal))
        {
            var name = template.Name.Replace("_GEN_VARIABLE", "");
            if (!seen.Add(name)) continue;
            collected.Add(Port(name, template));
        }
        current = SuperClass(current);
        currentExports = current?.Owner?.GetExports().ToList() ?? [];
    }
    return collected;
}

static bool IsConnectionTemplate(UObject export) => KindOf(export) is not null;

/// The Blueprint class this one derives from, or null once the super is native.
static UObject? SuperClass(UObject generatedClass)
{
    try { return (generatedClass as UStruct)?.SuperStruct?.Load(); }
    catch (Exception) { return null; }
}

object Port(string name, UObject template)
{
    var location = template.GetOrDefault("RelativeLocation", FVector.ZeroVector);
    var rotation = template.GetOrDefault("RelativeRotation", FRotator.ZeroRotator);
    var kind = KindOf(template)!;
    return new
    {
        name,
        kind,
        direction = DirectionOf(template, kind),
        translation = new[] { location.X, location.Y, location.Z },
        rotation = new[] { rotation.Pitch, rotation.Yaw, rotation.Roll },
        clearance = Number(template, "mConnectorClearance"),
    };
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

/// A connection's direction.
///
/// Belts and pipes each have their own enum and neither is serialised when it
/// equals the value the component's archetype carries. Every output spells
/// `FCD_OUTPUT` out, and the ten connections that really are bidirectional
/// spell `FCD_ANY` out; an *absent* `mDirection` is the one case the cooked
/// asset does not answer.
///
/// It is tempting to read an absent `mDirection` as the enum's zero, `FCD_INPUT`
/// -- and for a machine port it does come out that way -- but the default is the
/// archetype's value, not the enum's, and the archetype is a native constructor
/// the pak does not carry. `AFGBuildableConveyorBase`'s constructor sets its two
/// ends apart (`mConnection0` input, `mConnection1` output), so reading the zero
/// there labels every belt and lift end an input, which is what the registry
/// said before this. So an absent `mDirection` is reported as `"unknown"` and
/// `scripts/sfy_registry.py` resolves it, recording a `direction_source` per
/// port for how.
///
/// Pipes are not affected: `mPipeConnectionType`'s default really is the class
/// default `PCT_ANY` on every pipe archetype in the content. Power connections
/// are circuit connections and have no direction at all.
static string DirectionOf(UObject template, string kind)
{
    if (kind == "power") return "any";
    if (kind == "pipe")
    {
        var pipe = Text(template, "mPipeConnectionType") ?? "EPipeConnectionType::PCT_ANY";
        if (pipe.EndsWith("PCT_CONSUMER", StringComparison.Ordinal)) return "input";
        if (pipe.EndsWith("PCT_PRODUCER", StringComparison.Ordinal)) return "output";
        if (pipe.EndsWith("PCT_SNAP_ONLY", StringComparison.Ordinal)) return "snap_only";
        return "any";
    }
    var direction = Text(template, "mDirection");
    if (direction is null) return "unknown";
    if (direction.EndsWith("FCD_OUTPUT", StringComparison.Ordinal)) return "output";
    if (direction.EndsWith("FCD_ANY", StringComparison.Ordinal)) return "any";
    if (direction.EndsWith("FCD_SNAP_ONLY", StringComparison.Ordinal)) return "snap_only";
    return "input";
}

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

/// Maximum wire lengths, read from the game's own Docs.json class-default dump.
///
/// `AFGBuildableWire::mMaxLength` is a native default, so `Build_PowerLine_C`'s
/// cooked class default object does not carry it -- but Docs.json is a
/// reflection dump of those same class defaults and does.
static SortedDictionary<string, Dictionary<string, object?>> WireLengths(string gameDir)
{
    var wires = new SortedDictionary<string, Dictionary<string, object?>>(StringComparer.Ordinal);
    var docs = Path.Combine(gameDir, "CommunityResources", "Docs", "en-US.json");
    using var reader = new StreamReader(docs, detectEncodingFromByteOrderMarks: true);
    dynamic groups = JsonConvert.DeserializeObject(reader.ReadToEnd())!;
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
