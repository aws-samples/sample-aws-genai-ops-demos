"""
Runtime -> AWS Transform mapping (constants, not a config table).

This is AWS reference data, not per-deployment config, so it is a constant map
(consistent with discover.SUPPORT_HOME). Maps a source Lambda runtime *family*
to its managed transformation, default target runtime, and an optional
additionalPlanContext prefix. Nothing per-account to configure, so a config
table would be the wrong tool.

Python is the only family with a contextPrefix: AWS/lambda-nodejs-runtime-upgrade
and AWS/dotnet-lambda-upgrade are Lambda-specific, so they need no Lambda
explanation at all. AWS/python-version-upgrade is a generic language upgrade
that does not know the Lambda handler conventions, so the prefix states only
what a generic transformation cannot infer.

The defaultTarget of each family is the runtime its managed transformation is
built for; it is the only target the migration uses (migrate.resolve_and_verify).
"""

PYTHON_CONTEXT_PREFIX = (
    "This function runs on AWS Lambda. Its entry point is the handler named in the "
    "function configuration (<module>.<function>, signature (event, context)); "
    "keep that module path and function name unchanged. The runtime is set in the "
    "infrastructure template (the Runtime property in SAM or CloudFormation, the "
    "runtime field in CDK or Serverless Framework), update it to the target. "
    "Do not rename the handler, its module or the deployment package layout."
)

# family -> managed transformation details.
# buildCommand is empty for all three families in this phase (see design); it is
# a per-family constant so a future fixture needing one is a one-line change.
TRANSFORMS = {
    "python": {
        "transformName": "AWS/python-version-upgrade",
        "defaultTarget": "python3.13",
        "contextPrefix": PYTHON_CONTEXT_PREFIX,
        "buildCommand": "",
        "validationCommands": "python -m pytest",
    },
    "nodejs": {
        "transformName": "AWS/lambda-nodejs-runtime-upgrade",
        "defaultTarget": "nodejs24.x",
        "contextPrefix": None,  # Lambda-specific transform; no prefix needed
        "buildCommand": "",
        "validationCommands": "npm test",
    },
    "dotnet": {
        "transformName": "AWS/dotnet-lambda-upgrade",
        "defaultTarget": "dotnet10",
        "contextPrefix": None,  # Lambda-specific transform; no prefix needed
        "buildCommand": "",
        "validationCommands": "dotnet test",
    },
}
# validationCommands is the command atx runs AFTER the transform to validate the
# upgrade before producing a result (per the AWS "Automate Lambda runtime
# upgrades with AWS Transform custom" blog, which passes validationCommands=pytest).
# A green validation is the quality gate before the upgrade branch is pushed.
# NOTE: the AWS guidance assumes tests exist; what atx does when a repo has NO
# tests is NOT documented and is to be settled empirically by the first real
# run (the python fixture carries one test so the tested happy-path is
# exercised first).


def _family(runtime: str):
    """The transform family a runtime id belongs to, or None.

    Matches by lowercased prefix: python* -> python; nodejs* -> nodejs;
    dotnet* / dotnetcore* -> dotnet. Everything else (go1.x, ruby2.7, java8,
    provided.*, '', garbage) -> None. Pure."""
    if not isinstance(runtime, str):
        return None
    r = runtime.strip().lower()
    if not r:
        return None
    if r.startswith("python"):
        return "python"
    if r.startswith("nodejs"):
        return "nodejs"
    if r.startswith("dotnetcore") or r.startswith("dotnet"):
        return "dotnet"
    return None


def transform_for_runtime(runtime):
    """Map a Lambda runtime id (e.g. 'python3.9', 'nodejs16.x', 'dotnet6',
    'dotnetcore3.1') to its transform entry, or None => RUNTIME_UNSUPPORTED.

    Pure. The returned dict is the module constant (callers must not mutate it).
    """
    family = _family(runtime)
    if family is None:
        return None
    return TRANSFORMS[family]
