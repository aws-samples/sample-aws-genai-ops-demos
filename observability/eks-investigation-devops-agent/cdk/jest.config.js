module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  transform: {
    '^.+\\.tsx?$': 'ts-jest',
  },
  // Each property test synthesizes full CDK stacks — running in parallel
  // exhausts memory/CPU and causes hangs. Sequential execution is reliable.
  maxWorkers: 1,
  // shared/lab/cdk/*.ts lives outside this package: resolve aws-cdk-lib and
  // constructs from this project's node_modules (one copy, no instanceof drift).
  // Mirrors compilerOptions.paths in tsconfig.json.
  moduleNameMapper: {
    '^aws-cdk-lib$': '<rootDir>/node_modules/aws-cdk-lib',
    '^aws-cdk-lib/(.*)$': '<rootDir>/node_modules/aws-cdk-lib/$1',
    '^constructs$': '<rootDir>/node_modules/constructs',
  },
};
