// Discovery-only placeholder: this source is NEVER compiled or invoked. The mock
// .NET function exists purely so the demo's code-behind detection resolves a
// deprecated-runtime (dotnet6) function to the fixture CodeCommit repository.
// A real dotnet6 Lambda needs a compiled DLL + bootstrap, which we cannot build
// here without a .NET toolchain (and Docker bundling is not allowed). The Lambda
// create call only validates the runtime id and that a package exists; it does
// not validate this handler. See mock-fixture-stack.ts for the full note.
using Amazon.Lambda.Core;

namespace MockDotnet
{
    public class Function
    {
        public string Handler(object input, ILambdaContext context)
        {
            return "hello from deprecated dotnet6 mock";
        }
    }
}
