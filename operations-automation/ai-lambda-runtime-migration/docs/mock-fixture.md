# Mock fixture

The mock fixture is optional test data for the **Migrate** step. It gives AWS Transform three real functions to upgrade without touching your own repositories. Deploy it with `deploy-all -Mock` / `--mock`, or on its own with `scripts/deploy-mock` (see [Mock fixture (optional)](../README.md#mock-fixture-optional) in the README).

## What it deploys

The `LambdaRuntimeMigrationMock-<region>` stack creates:

- An AWS CodeCommit repository, `lambda-migration-demo-fixture`, seeded on `main` from [`cdk/mock-fixtures/`](../cdk/mock-fixtures/).
- Three Lambda functions on deprecated runtimes: `lambda-migration-mock-nodejs` (nodejs16.x), `lambda-migration-mock-python` (python3.9) and `lambda-migration-mock-dotnet` (dotnet6).
- Two tags on each function, `source-repo=lambda-migration-demo-fixture` and `git-branch=main`. With the default Settings, Migrate uses them to find the function's repository and branch.

The Node.js and Python functions run the code that is in the repository. The .NET function ships the project source without compiling it: the demo only needs Trusted Advisor to see a dotnet6 function, and it is never invoked.

## How each function is built

The three functions follow the same approach:

- **Realistic code for its runtime.** Each one is a small business function written the way it typically was for its runtime, using APIs that were later removed or blocked. Every pattern works on today's runtime and breaks on the target runtime. Deprecation warnings alone don't count.
- **A test that proves it.** The tests pass on the current runtime and fail on the target runtime until the code is fixed. AWS Transform runs them to validate its change (`validationCommands`, set per family in [`backend/transform_runtimes.py`](../backend/transform_runtimes.py)).
- **Code and infrastructure together.** Each folder holds the function's AWS SAM `template.yaml`, the way many teams keep code and IaC side by side. AWS Transform is expected to update its `Runtime` line along with the code. The demo itself deploys the functions with CDK, not with these templates.

The repository holds three functions, and AWS Transform runs the validation commands from the repository root. So the root carries the entry points: `package.json` (its `npm test` installs and tests `nodejs/`) and `MockFunctions.sln` (for `dotnet test`). `python -m pytest` finds `python/` on its own. A repository with one function per repository wouldn't need them.

## Node.js 16 → 24: order token service

[`nodejs/index.js`](../cdk/mock-fixtures/nodejs/index.js) decrypts an order token sent by a partner, validates the order and returns a summary. The token secret comes from SSM Parameter Store.

| Code in the fixture | On Node.js 16 | On the nodejs24.x runtime | Reference |
|---|---|---|---|
| `crypto.createCipher('aes192', secret)` / `createDecipher` | Works (deprecation warning) | Removed in Node.js 22: `TypeError` | [DEP0106](https://nodejs.org/api/deprecations.html#DEP0106) |
| `util.isString()`, `util.isNumber()`, `util.isDate()`, `util.log()` | Works | Removed in Node.js 23 | [DEP0047–DEP0059](https://nodejs.org/api/deprecations.html#DEP0047) |
| `require('aws-sdk')` (AWS SDK for JavaScript v2), a `devDependency` only | Bundled by the runtime | Not bundled since nodejs18.x, which bundles v3 (`@aws-sdk/*`) | [Lambda Node.js runtimes](https://docs.aws.amazon.com/lambda/latest/dg/lambda-nodejs.html) |
| `exports.handler = (event, context, callback) => …` | Works | Callback handlers are not supported | [Node.js 24 runtime announcement](https://aws.amazon.com/about-aws/whats-new/2025/11/aws-lambda-nodejs-24) |

The last two rows are rules of the Lambda runtime, not of Node.js itself. [`test/lambda-runtime.js`](../cdk/mock-fixtures/nodejs/test/lambda-runtime.js) models them from the Node.js version the tests run on, so the tests fail the way the deployed function would.

What AWS Transform changed on our test run (all 7 tests pass on Node.js 24):

- `crypto.createCipheriv` / `createDecipheriv` with `aes-192-cbc`, a key derived by `crypto.scryptSync` and a random IV prepended to the token.
- `typeof` checks instead of `util.isString` / `util.isNumber`, and `console.log` instead of `util.log`.
- `@aws-sdk/client-ssm` (v3, `GetParameterCommand`) as a production dependency instead of `aws-sdk` v2, plus a generated `package-lock.json`.
- An `async` handler, `engines` set to `>=24`, and `Runtime: nodejs24.x` in `template.yaml`.

Worth reviewing in the pull request: the new cipher changes the token format, so tokens issued before the change no longer decrypt, and the key salt is a fixed string.

## Python 3.9 → 3.13: partner webhook receiver

[`python/lambda_function.py`](../cdk/mock-fixtures/python/lambda_function.py) accepts a JSON webhook, checks the content type and the partner's client version, runs an optional enrichment plugin and returns the accepted event.

| Code in the fixture | On Python 3.9 | On Python 3.13 | Reference |
|---|---|---|---|
| `from distutils.version import LooseVersion` to compare client versions | Works | `distutils` removed in 3.12 | [PEP 632](https://peps.python.org/pep-0632/) |
| `import imp` + `imp.load_source()` for the plugin | Works | `imp` removed in 3.12 | [What's new in 3.12](https://docs.python.org/3/whatsnew/3.12.html) |
| `configparser.SafeConfigParser()` for `settings.ini` | Works (deprecation warning) | Removed in 3.12 | [What's new in 3.12](https://docs.python.org/3/whatsnew/3.12.html) |
| `import cgi` + `cgi.parse_header()` for `Content-Type` | Works | `cgi` removed in 3.13 | [PEP 594](https://peps.python.org/pep-0594/) |
| `self.assertEquals(...)` in [`test_handler.py`](../cdk/mock-fixtures/python/test_handler.py) | Works (deprecation warning) | unittest aliases removed in 3.12 | [What's new in 3.12](https://docs.python.org/3/whatsnew/3.12.html) |

The last row means AWS Transform has to upgrade the tests as well as the code.

What AWS Transform changed on our test run (all 6 tests pass on Python 3.13):

- A small numeric version comparison in place of `LooseVersion`, so no new dependency.
- `importlib.util.spec_from_file_location` for the plugin, `configparser.ConfigParser`, and `email.message.Message` to parse `Content-Type`.
- `assertEqual` in the tests, and `Runtime: python3.13` in `template.yaml`.

Worth reviewing in the pull request: the template description now reads "on a deprecated runtime (python3.13)", and the run also updated the repository `README.md`. On an interpreter where `setuptools` is installed, its `distutils` shim can hide the first row of the table.

## .NET 6 → 10: partner session gateway

[`dotnet/src/MockDotnet/Function.cs`](../cdk/mock-fixtures/dotnet/src/MockDotnet/Function.cs) checks a partner's client certificate, derives the partner's API key from a password and caches the session for the next call.

| Code in the fixture | On .NET 6 | On .NET 10 | Reference |
|---|---|---|---|
| `BinaryFormatter` to serialize the cached session | Works (SYSLIB0011, suppressed in the project) | Always throws `PlatformNotSupportedException` since .NET 9 | [BinaryFormatter removed from .NET 9](https://devblogs.microsoft.com/dotnet/binaryformatter-removed-from-dotnet-9) |
| `new X509Certificate2(byte[])` to read the certificate | Works | Obsolete since .NET 9 (SYSLIB0057) | [SYSLIB0057](https://learn.microsoft.com/dotnet/fundamentals/syslib-diagnostics/syslib0057) |
| `new Rfc2898DeriveBytes(password, salt)` (implicit SHA-1, 1000 iterations) | Works | Obsolete since .NET 7 (SYSLIB0041) | [SYSLIB0041](https://learn.microsoft.com/dotnet/fundamentals/syslib-diagnostics/syslib0041) |
| `net6.0` target framework, .NET 6-era `Amazon.Lambda.Core` 2.1.0 and `Amazon.Lambda.Serialization.SystemTextJson` 2.3.0 | Builds | Target framework and packages to upgrade | — |

The project sets `TreatWarningsAsErrors`, a common team policy, so the two obsolete APIs fail the build on .NET 10. The xUnit tests in [`dotnet/test/MockDotnet.Tests`](../cdk/mock-fixtures/dotnet/test/MockDotnet.Tests) cover serialization, key derivation, the certificate and the handler.

What AWS Transform changed on our test run (all 5 tests pass on .NET 10):

- `System.Text.Json` for the session, and the `SYSLIB0011` suppression removed.
- `X509CertificateLoader.LoadCertificate`, and `Rfc2898DeriveBytes.Pbkdf2` with SHA-256 and 210,000 iterations.
- `net10.0` for both projects, `Amazon.Lambda.Core` 3.4.0, `Amazon.Lambda.Serialization.SystemTextJson` 3.0.1 and current xUnit packages.
- `Runtime: dotnet10` in `template.yaml`, and a `global.json` at the repository root pinning the .NET 10 SDK.

Worth reviewing in the pull request: the new serializer and key derivation change what existing sessions and API keys look like, and `global.json` applies to the whole repository, not only this function.

## What to check after a migration

Each migration pushes a branch `lambda-migration/<target>-<job>` to the fixture repository. `main` is never changed. In the CodeCommit console, compare the branch with `main`:

- The code changes for every row of the function's table above.
- The test changes, where the old tests used removed APIs (Python).
- `Runtime` updated in the function's `template.yaml`.
- No change in the two other function folders. On our run the only changes outside a function's folder were repository-level: the root `README.md` (Python, .NET) and `global.json` (.NET).

AWS Transform is an AI agent, so another run can produce different code. Its validation runs the tests on the target runtime before the branch is pushed, and the build log in CodeBuild shows each step.

## Cost and cleanup

Each migration is one AWS Transform run in one CodeBuild build. Measured on our test run in eu-west-3, where AWS Transform ran in the eu-central-1 fallback region:

| Function | AWS Transform agent-minutes | CodeBuild duration | AWS Transform cost ($0.035 per agent-minute) |
|---|---|---|---|
| Python 3.9 → 3.13 | 14.2 | 3 min 52 s | ~$0.50 |
| Node.js 16 → 24 | 19.1 | 4 min 26 s | ~$0.67 |
| .NET 6 → 10 | 31.3 | 5 min 33 s | ~$1.10 |

Add the CodeBuild minutes at your region's rate. See [Cost Estimate](../README.md#cost-estimate).

Remove the fixture with `scripts/deploy-mock -Destroy` / `--destroy`. It deletes the repository (with its upgrade branches) and the three functions.
