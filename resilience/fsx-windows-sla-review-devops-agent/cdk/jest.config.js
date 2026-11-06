module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  transform: { '^.+\\.tsx?$': 'ts-jest' },
  // The shared constructs live outside this package: resolve aws-cdk-lib and constructs
  // from this project's node_modules (one copy, no instanceof drift). Mirrors tsconfig paths.
  moduleNameMapper: {
    '^aws-cdk-lib$': '<rootDir>/node_modules/aws-cdk-lib',
    '^aws-cdk-lib/(.*)$': '<rootDir>/node_modules/aws-cdk-lib/$1',
    '^constructs$': '<rootDir>/node_modules/constructs',
  },
};
