using System.Globalization;
using System.Security.Cryptography;
using CUE4Parse.MappingsProvider;
using CUE4Parse.MappingsProvider.Usmap;
using Newtonsoft.Json;

namespace SfyExtract;

/// <summary>
/// Writes <c>src/flab2bp/sfy/data/struct_schemas.json</c>: the field list of
/// every struct the blueprint corpus carries, straight out of the game's
/// shipped <c>FactoryGame.usmap</c>.
///
/// The usmap is the engine's own reflection data, so this needs no package and
/// no mount -- <see cref="FileUsmapTypeMappingsProvider"/> parses the file and
/// hands back <see cref="TypeMappings.Types"/>, a name -> <see cref="Struct"/>
/// dictionary. Each <see cref="Struct"/> carries <c>SuperType</c> (the parent's
/// name, or null) and <c>Properties</c>, an index -> <see cref="PropertyInfo"/>
/// map; a <see cref="PropertyInfo"/> carries <c>Name</c>, <c>ArraySize</c> and
/// <c>MappingType</c>, and a <see cref="PropertyType"/> carries <c>Type</c>
/// (already the <c>*Property</c> tag spelling the save file writes, because
/// CUE4Parse builds it from <c>EPropertyType.ToString()</c>), <c>StructType</c>,
/// <c>EnumName</c>, and <c>InnerType</c>/<c>ValueType</c> for containers.
///
/// Only the names asked for and their super chains are emitted: the file is a
/// check on what the corpus actually contains, not a dump of the 30,000-odd
/// structs the mappings describe.
/// </summary>
public static class StructSchemas
{
    public static int Write(string mappingsPath, string usmapPath, string namesPath, string outPath)
    {
        var forGame = new FileUsmapTypeMappingsProvider(mappingsPath).MappingsForGame
            ?? throw new InvalidDataException($"{mappingsPath} parsed to no mappings at all");
        var types = forGame.Types;
        Console.Error.WriteLine($"{types.Count} struct types in the mappings");

        var wanted = File.ReadAllLines(namesPath)
            .Select(line => line.Trim())
            .Where(line => line.Length > 0 && !line.StartsWith('#'))
            .ToList();

        var schemas = new SortedDictionary<string, object>(StringComparer.Ordinal);
        var missing = new SortedSet<string>(StringComparer.Ordinal);
        var queue = new Queue<string>(wanted);
        while (queue.Count > 0)
        {
            var name = queue.Dequeue();
            if (schemas.ContainsKey(name) || missing.Contains(name)) continue;
            if (!types.TryGetValue(name, out var struc))
            {
                missing.Add(name);
                continue;
            }
            // A struct with no parent has SuperType null; an empty string would
            // be a name no lookup could ever resolve, so it is null here too.
            var super = string.IsNullOrEmpty(struc.SuperType) ? null : struc.SuperType;
            if (super is not null) queue.Enqueue(super);
            schemas[name] = new Dictionary<string, object?>
            {
                ["super"] = super,
                ["fields"] = Fields(struc),
            };
        }

        foreach (var name in missing)
            Console.Error.WriteLine($"WARNING: no usmap struct named {name}");

        var output = new
        {
            provenance = new
            {
                cue4parse = typeof(FileUsmapTypeMappingsProvider).Assembly.GetName().Version?.ToString(),
                extracted = DateTime.UtcNow.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                usmap = Path.GetFileName(usmapPath),
                usmap_sha256 = Sha256(usmapPath),
                usmap_types = types.Count,
                requested = wanted.Count,
                missing = missing.ToList(),
            },
            structs = schemas,
        };
        File.WriteAllText(outPath, JsonConvert.SerializeObject(output, Formatting.Indented) + "\n");
        Console.Error.WriteLine(
            $"wrote {outPath}: {schemas.Count} schemas from {wanted.Count} names, {missing.Count} missing");
        return missing.Count == 0 ? 0 : 1;
    }

    /// <summary>A struct's own properties, in declaration order, one entry per name.</summary>
    /// <remarks>
    /// The mappings key properties by index, and a fixed-size <c>UPROPERTY</c>
    /// array such as <c>WireInstance::Locations[2]</c> occupies one
    /// index per element -- the same name, the same type, repeated. The file
    /// carries it once, with <c>array_size</c>, because a property tag names it
    /// once and distinguishes the elements by its own index field.
    /// </remarks>
    private static List<Dictionary<string, object?>> Fields(Struct struc)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        return struc.Properties
            .OrderBy(p => p.Key)
            .Where(p => seen.Add(p.Value.Name))
            .Select(p => Field(p.Value))
            .ToList();
    }

    /// <summary>One property, as the schema file spells it.</summary>
    /// <remarks>
    /// The optional keys mirror what a save-file property tag carries beside its
    /// type: a struct's name, an enum's name, and a container's element and
    /// value types. They are written only where the mappings have them, so a
    /// plain <c>IntProperty</c> is two keys and nothing else.
    /// </remarks>
    private static Dictionary<string, object?> Field(PropertyInfo info)
    {
        var field = new Dictionary<string, object?>
        {
            ["name"] = info.Name,
            ["type"] = info.MappingType.Type,
        };
        Describe(field, info.MappingType);
        if (info.ArraySize is > 1) field["array_size"] = info.ArraySize;
        return field;
    }

    private static void Describe(Dictionary<string, object?> field, PropertyType type)
    {
        if (!string.IsNullOrEmpty(type.StructType)) field["struct"] = type.StructType;
        if (!string.IsNullOrEmpty(type.EnumName)) field["enum"] = type.EnumName;
        if (type.InnerType is { } inner) field["inner"] = Nested(inner);
        if (type.ValueType is { } value) field["value"] = Nested(value);
    }

    private static Dictionary<string, object?> Nested(PropertyType type)
    {
        var nested = new Dictionary<string, object?> { ["type"] = type.Type };
        Describe(nested, type);
        return nested;
    }

    private static string Sha256(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexStringLower(SHA256.HashData(stream));
    }
}
