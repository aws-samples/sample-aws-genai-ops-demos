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
};
