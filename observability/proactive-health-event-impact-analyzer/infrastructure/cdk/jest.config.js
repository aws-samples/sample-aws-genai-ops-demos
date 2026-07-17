module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/../../test'],
  testMatch: ['**/*.test.ts'],
  transform: {
    '^.+\\.tsx?$': ['ts-jest', {
      tsconfig: {
        target: 'ES2020',
        module: 'commonjs',
        lib: ['es2020'],
        strict: true,
        esModuleInterop: true,
        skipLibCheck: true,
        resolveJsonModule: true,
        rootDir: '../..',
        baseUrl: '.',
        paths: {
          'aws-cdk-lib': ['./node_modules/aws-cdk-lib'],
          'aws-cdk-lib/*': ['./node_modules/aws-cdk-lib/*'],
          'constructs': ['./node_modules/constructs'],
          '@aws-sdk/*': ['./node_modules/@aws-sdk/*'],
          'fast-check': ['./node_modules/fast-check/lib/types57/fast-check'],
        },
      },
    }],
  },
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx', 'json'],
  moduleNameMapper: {
    '^aws-cdk-lib(.*)$': '<rootDir>/node_modules/aws-cdk-lib$1',
    '^constructs$': '<rootDir>/node_modules/constructs',
    '^@aws-sdk/(.*)$': '<rootDir>/node_modules/@aws-sdk/$1',
    '^fast-check$': '<rootDir>/node_modules/fast-check/lib/cjs/fast-check.js',
  },
};
