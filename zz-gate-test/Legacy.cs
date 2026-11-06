// THROWAWAY fixture. Semgrep insecure-binaryformatter-deserialization, 6.0 (medium): expected DROPPED.
using System.IO;
using System.Runtime.Serialization.Formatters.Binary;

public class Legacy
{
    public object Load(Stream s)
    {
        var formatter = new BinaryFormatter();
        return formatter.Deserialize(s);
    }
}
