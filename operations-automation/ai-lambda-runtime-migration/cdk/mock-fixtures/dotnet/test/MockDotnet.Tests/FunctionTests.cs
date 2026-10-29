using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using MockDotnet;
using Xunit;

namespace MockDotnet.Tests;

public class FunctionTests
{
    private static byte[] SelfSignedCertificate()
    {
        using var rsa = RSA.Create(2048);
        var request = new CertificateRequest("CN=partner.example.com", rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        using var cert = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(30));
        return cert.Export(X509ContentType.Cert);
    }

    [Fact]
    public void SessionRoundTrips()
    {
        var session = new PartnerSession { PartnerId = "partner-42", Thumbprint = "ABC", IssuedAt = DateTime.UtcNow };
        var restored = Function.DeserializeSession(Function.SerializeSession(session));
        Assert.Equal(session.PartnerId, restored.PartnerId);
        Assert.Equal(session.Thumbprint, restored.Thumbprint);
    }

    [Fact]
    public void ApiKeyIsStablePerPassword()
    {
        Assert.Equal(Function.DeriveApiKey("s3cret"), Function.DeriveApiKey("s3cret"));
        Assert.NotEqual(Function.DeriveApiKey("s3cret"), Function.DeriveApiKey("other"));
    }

    [Fact]
    public void ThumbprintIsRead()
    {
        Assert.Equal(40, Function.CertificateThumbprint(SelfSignedCertificate()).Length);
    }

    [Fact]
    public void HandlerIssuesAndReusesASession()
    {
        var function = new Function();
        var request = new SessionRequest
        {
            PartnerId = "partner-42",
            Password = "s3cret",
            CertificateBase64 = Convert.ToBase64String(SelfSignedCertificate()),
        };
        var first = function.Handler(request, null!);
        Assert.Equal(200, first.StatusCode);

        var second = function.Handler(new SessionRequest { PartnerId = "partner-42", Password = "s3cret", CachedSession = first.Session }, null!);
        Assert.Equal(200, second.StatusCode);
        Assert.Equal(first.ApiKey, second.ApiKey);
    }

    [Fact]
    public void HandlerRequiresACertificate()
    {
        var response = new Function().Handler(new SessionRequest { PartnerId = "partner-42", Password = "s3cret" }, null!);
        Assert.Equal(401, response.StatusCode);
    }
}
