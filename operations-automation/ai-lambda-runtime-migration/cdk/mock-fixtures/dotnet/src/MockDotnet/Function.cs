// Partner session gateway (mock fixture, dotnet6).
//
// A partner presents its client certificate and a password. The function
// checks the certificate thumbprint, derives the partner's API key and caches
// the session so the next call can skip the checks.
//
// Written the way a .NET 6 Lambda function typically was, ON PURPOSE: every
// pattern below works on .NET 6 and breaks on .NET 10. AWS Transform is
// expected to find and fix each one (see docs/mock-fixture.md in the demo):
//   - BinaryFormatter: always throws PlatformNotSupportedException since .NET 9
//   - new X509Certificate2(byte[]): obsolete since .NET 9 (SYSLIB0057), a build
//     error with warnings-as-errors
//   - new Rfc2898DeriveBytes(password, salt): obsolete since .NET 7 (SYSLIB0041,
//     implicit SHA-1 and 1000 iterations), a build error with warnings-as-errors
//   - net6.0 target framework and .NET 6-era Amazon.Lambda.* packages
using System.Runtime.Serialization.Formatters.Binary;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using Amazon.Lambda.Core;

[assembly: LambdaSerializer(typeof(Amazon.Lambda.Serialization.SystemTextJson.DefaultLambdaJsonSerializer))]

namespace MockDotnet;

[Serializable]
public class PartnerSession
{
    public string PartnerId { get; set; } = "";
    public string Thumbprint { get; set; } = "";
    public DateTime IssuedAt { get; set; }
}

public class SessionRequest
{
    public string PartnerId { get; set; } = "";
    public string Password { get; set; } = "";
    public string CertificateBase64 { get; set; } = "";
    public string? CachedSession { get; set; }
}

public class SessionResponse
{
    public int StatusCode { get; set; }
    public string ApiKey { get; set; } = "";
    public string Session { get; set; } = "";
    public string Error { get; set; } = "";
}

public class Function
{
    private static readonly byte[] Salt = Encoding.UTF8.GetBytes("partner-gateway-salt");

    public static byte[] SerializeSession(PartnerSession session)
    {
        var formatter = new BinaryFormatter();
        using var stream = new MemoryStream();
        formatter.Serialize(stream, session);
        return stream.ToArray();
    }

    public static PartnerSession DeserializeSession(byte[] data)
    {
        var formatter = new BinaryFormatter();
        using var stream = new MemoryStream(data);
        return (PartnerSession)formatter.Deserialize(stream);
    }

    public static string DeriveApiKey(string password)
    {
        using var kdf = new Rfc2898DeriveBytes(password, Salt);
        return Convert.ToBase64String(kdf.GetBytes(32));
    }

    public static string CertificateThumbprint(byte[] certificate)
    {
        using var cert = new X509Certificate2(certificate);
        return cert.Thumbprint;
    }

    public SessionResponse Handler(SessionRequest request, ILambdaContext context)
    {
        if (!string.IsNullOrEmpty(request.CachedSession))
        {
            var cached = DeserializeSession(Convert.FromBase64String(request.CachedSession));
            if (cached.PartnerId == request.PartnerId && cached.IssuedAt > DateTime.UtcNow.AddHours(-1))
            {
                return new SessionResponse { StatusCode = 200, ApiKey = DeriveApiKey(request.Password), Session = request.CachedSession };
            }
        }

        if (string.IsNullOrEmpty(request.CertificateBase64))
        {
            return new SessionResponse { StatusCode = 401, Error = "a client certificate is required" };
        }

        var session = new PartnerSession
        {
            PartnerId = request.PartnerId,
            Thumbprint = CertificateThumbprint(Convert.FromBase64String(request.CertificateBase64)),
            IssuedAt = DateTime.UtcNow,
        };
        context?.Logger?.LogLine($"issued a session for partner {session.PartnerId}");
        return new SessionResponse
        {
            StatusCode = 200,
            ApiKey = DeriveApiKey(request.Password),
            Session = Convert.ToBase64String(SerializeSession(session)),
        };
    }
}
