import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';

const PROJECT_ROOT = path.resolve(__dirname, '../..');

/**
 * Documentation and project structure tests.
 * Validates: Requirements 14.1, 14.2, 14.3, 14.4
 */

describe('Documentation and Structure', () => {
  // -------------------------------------------------------------------------
  // 1. README.md exists with required sections
  // Validates: Requirement 14.1
  // -------------------------------------------------------------------------
  describe('README.md', () => {
    const readmePath = path.join(PROJECT_ROOT, 'README.md');

    it('should exist at project root', () => {
      expect(fs.existsSync(readmePath)).toBe(true);
    });

    it('should contain an Overview section', () => {
      const content = fs.readFileSync(readmePath, 'utf-8');
      expect(content).toMatch(/^## Overview/m);
    });

    it('should contain a Prerequisites section', () => {
      const content = fs.readFileSync(readmePath, 'utf-8');
      expect(content).toMatch(/^## Prerequisites/m);
    });

    it('should contain a Deployment section', () => {
      const content = fs.readFileSync(readmePath, 'utf-8');
      expect(content).toMatch(/^## Deployment/m);
    });

    it('should contain an Architecture section', () => {
      const content = fs.readFileSync(readmePath, 'utf-8');
      expect(content).toMatch(/architecture/i);
    });

    it('should contain a Troubleshooting section', () => {
      const content = fs.readFileSync(readmePath, 'utf-8');
      expect(content).toMatch(/^## Troubleshooting/m);
    });

    it('should contain a Contributing section', () => {
      const content = fs.readFileSync(readmePath, 'utf-8');
      expect(content).toMatch(/^## Contributing/m);
    });

    it('should contain a Security section', () => {
      const content = fs.readFileSync(readmePath, 'utf-8');
      expect(content).toMatch(/^## Security/m);
    });

    it('should contain a License section', () => {
      const content = fs.readFileSync(readmePath, 'utf-8');
      expect(content).toMatch(/^## License/m);
    });
  });

  // -------------------------------------------------------------------------
  // 2. ARCHITECTURE.md exists with architecture diagrams
  // Validates: Requirement 14.2
  // -------------------------------------------------------------------------
  describe('ARCHITECTURE.md', () => {
    const archPath = path.join(PROJECT_ROOT, 'ARCHITECTURE.md');

    it('should exist at project root', () => {
      expect(fs.existsSync(archPath)).toBe(true);
    });

    it('should contain architecture diagram content', () => {
      const content = fs.readFileSync(archPath, 'utf-8');
      // Mermaid diagrams or ASCII art diagrams
      const hasMermaid = content.includes('```mermaid');
      const hasAsciiDiagram = /[┌┐└┘│─▼►▲◄╔╗╚╝║═]/.test(content);
      expect(hasMermaid || hasAsciiDiagram).toBe(true);
    });

    it('should describe the multi-agent architecture', () => {
      const content = fs.readFileSync(archPath, 'utf-8');
      expect(content).toMatch(/orchestrat/i);
      expect(content).toMatch(/agent/i);
    });
  });

  // -------------------------------------------------------------------------
  // 3. Directory structure matches convention
  // Validates: Requirement 14.3
  // -------------------------------------------------------------------------
  describe('Directory structure', () => {
    const requiredDirs = [
      'agents',
      'frontend',
      'infrastructure/cdk',
      'tests',
      'scripts',
    ];

    for (const dir of requiredDirs) {
      it(`should have required directory: ${dir}/`, () => {
        const dirPath = path.join(PROJECT_ROOT, dir);
        expect(fs.existsSync(dirPath)).toBe(true);
        expect(fs.statSync(dirPath).isDirectory()).toBe(true);
      });
    }

    it('should have deployment scripts at project root', () => {
      expect(fs.existsSync(path.join(PROJECT_ROOT, 'deploy-all.ps1'))).toBe(true);
      expect(fs.existsSync(path.join(PROJECT_ROOT, 'deploy-all.sh'))).toBe(true);
    });
  });

  // -------------------------------------------------------------------------
  // 4. Standardized Contributing/Security/License sections reference root files
  // Validates: Requirement 14.4
  // -------------------------------------------------------------------------
  describe('Standardized README sections reference root files', () => {
    const readmePath = path.join(PROJECT_ROOT, 'README.md');

    it('Contributing section should reference ../../CONTRIBUTING.md', () => {
      const content = fs.readFileSync(readmePath, 'utf-8');
      expect(content).toContain('../../CONTRIBUTING.md');
    });

    it('Security section should reference ../../CONTRIBUTING.md#security-issue-notifications', () => {
      const content = fs.readFileSync(readmePath, 'utf-8');
      expect(content).toContain('../../CONTRIBUTING.md#security-issue-notifications');
    });

    it('License section should reference ../../LICENSE', () => {
      const content = fs.readFileSync(readmePath, 'utf-8');
      expect(content).toContain('../../LICENSE');
    });

    it('root CONTRIBUTING.md should exist', () => {
      const rootContributing = path.resolve(PROJECT_ROOT, '../../CONTRIBUTING.md');
      expect(fs.existsSync(rootContributing)).toBe(true);
    });

    it('root LICENSE should exist', () => {
      const rootLicense = path.resolve(PROJECT_ROOT, '../../LICENSE');
      expect(fs.existsSync(rootLicense)).toBe(true);
    });
  });
});
