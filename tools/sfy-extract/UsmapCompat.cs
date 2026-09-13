namespace SfyExtract;

/// <summary>
/// Makes the game's shipped <c>FactoryGame.usmap</c> readable by CUE4Parse.
///
/// The dumper that produced it writes <c>OptionalProperty</c> (type 28) as a
/// leaf -- the byte is not followed by the wrapped inner property type. UE5.6's
/// own format, and therefore CUE4Parse, reads an inner type there, so
/// CUE4Parse's parser desynchronises on the first such property and dies with
/// an index-out-of-range deep inside the struct table.
///
/// Every occurrence in the 1.2.0 mappings sits on an Engine, editor or plugin
/// class (<c>OptionalPropertyTestObject</c>, <c>ToolMenu</c>, <c>NiagaraSystem</c>
/// and the like) -- no <c>FactoryGame</c> class uses one. So we rewrite each of
/// those type bytes in place to <c>Utf8StrProperty</c> (29), which CUE4Parse
/// also reads as a leaf. The patch is one byte per occurrence, changes no
/// offsets, and leaves the trailing extension chunks valid.
/// </summary>
public static class UsmapCompat
{
    private const byte OptionalProperty = 28;
    private const byte Utf8StrProperty = 29;

    /// <summary>Write a CUE4Parse-readable copy of <paramref name="path"/> and return it.</summary>
    /// <remarks>Returns <paramref name="path"/> unchanged when nothing needed patching.</remarks>
    public static string Patch(string path, out int patched)
    {
        var bytes = File.ReadAllBytes(path);
        patched = PatchInPlace(bytes);
        if (patched == 0) return path;
        var outPath = Path.Combine(Path.GetTempPath(), Path.GetFileNameWithoutExtension(path) + ".patched.usmap");
        File.WriteAllBytes(outPath, bytes);
        return outPath;
    }

    private static int PatchInPlace(byte[] b)
    {
        var r = new Cursor(b);
        if (r.U16() != 0x30C4) throw new InvalidDataException("not a .usmap file");
        var version = r.U8();
        if (version >= 1 && r.I32() != 0)   // package versioning block
        {
            r.Skip(8);                      // FPackageFileVersion
            r.Skip(r.I32() * 20);           // FCustomVersion[] (guid + int32)
            r.Skip(4);                      // network compatible changelist
        }
        var compression = r.U8();
        if (compression != 0)
            throw new InvalidDataException($"usmap body uses compression method {compression}; only uncompressed is supported");
        var compressedSize = r.I32();
        var decompressedSize = r.I32();
        if (compressedSize != decompressedSize)
            throw new InvalidDataException("uncompressed usmap disagrees with itself about its body size");

        var names = r.I32();
        for (var i = 0; i < names; i++) r.Skip(version >= 2 ? r.U16() : r.U8());
        var enums = r.I32();
        for (var i = 0; i < enums; i++)
        {
            r.Skip(4);
            var entries = version >= 3 ? r.U16() : r.U8();
            r.Skip(entries * 4);
            if (version >= 4) r.Skip(entries * 4);   // explicit enum values
        }

        var patched = 0;
        var structs = r.I32();
        for (var i = 0; i < structs; i++)
        {
            r.Skip(8);                       // name + super name
            r.U16();                         // total property count
            var serializable = r.U16();
            for (var j = 0; j < serializable; j++)
            {
                r.Skip(7);                   // schema index, array size, name
                patched += SkipPropertyType(r, b);
            }
        }
        return patched;
    }

    /// <summary>Walk one property type tree, rewriting Optional leaves; returns how many.</summary>
    private static int SkipPropertyType(Cursor r, byte[] b)
    {
        var at = r.Offset;
        var type = r.U8();
        switch (type)
        {
            case OptionalProperty:
                b[at] = Utf8StrProperty;
                return 1;
            case 9:                          // StructProperty: struct name
                r.Skip(4);
                return 0;
            case 8 or 25:                    // Array, Set: one inner
                return SkipPropertyType(r, b);
            case 24:                         // Map: key then value
                return SkipPropertyType(r, b) + SkipPropertyType(r, b);
            case 26:                         // Enum: inner, then enum name
                var n = SkipPropertyType(r, b);
                r.Skip(4);
                return n;
            default:
                return 0;
        }
    }

    private sealed class Cursor(byte[] data)
    {
        public int Offset { get; private set; }

        public byte U8() => data[Offset++];

        public ushort U16()
        {
            var v = BitConverter.ToUInt16(data, Offset);
            Offset += 2;
            return v;
        }

        public int I32()
        {
            var v = BitConverter.ToInt32(data, Offset);
            Offset += 4;
            return v;
        }

        public void Skip(int n) => Offset += n;
    }
}
