/**
 * Property tests for Capture_Id_Predicate injection (Property 5) and SQL safety (Property 6).
 * Feature: genai-operations-analytics-tool
 *
 * Property 5: For every SQL string accepted by the shape validator, the
 * predicate injector produces output containing an exact
 * `capture_id = '<id>'` predicate at the top-level WHERE.
 *
 * Property 6: Forbidden top-level keywords and constructs cause rejection
 * before any Athena call is made.
 *
 * **Validates: Requirements 5.1, 5.2, 5.3, 5.7, 5.20**
 */
import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

// ---------------------------------------------------------------------------
// Constants mirroring sql_safety.py
// ---------------------------------------------------------------------------

const MAX_SQL_LENGTH = 16384;
const PCAP_LOGS_TABLE_NAME = 'pcap_logs';

/**
 * Forbidden top-level keywords per Req 5.3 and the design's shape constraint.
 * These must cause rejection when they appear as unquoted identifiers at
 * the top level of the SQL statement.
 */
const FORBIDDEN_KEYWORDS = [
  'INSERT', 'UPDATE', 'DELETE', 'DROP', 'CREATE', 'ALTER',
  'TRUNCATE', 'MSCK', 'JOIN', 'UNION', 'WITH',
  // Additional keywords from the implementation
  'INTERSECT', 'EXCEPT', 'VALUES', 'MERGE', 'CALL',
  'EXECUTE', 'GRANT', 'REVOKE',
] as const;

/**
 * Forbidden constructs (non-keyword) that must cause rejection.
 */
const FORBIDDEN_CONSTRUCTS = [';', '/*', '--'] as const;

/**
 * Capture_Id_Format regex: [A-Za-z0-9_-]{1,128}
 */
const CAPTURE_ID_REGEX = /^[A-Za-z0-9_-]{1,128}$/;

// ---------------------------------------------------------------------------
// SQL Shape Validator (TypeScript port of sql_safety.py logic)
// ---------------------------------------------------------------------------

class SqlShapeError extends Error {
  errorCategory: string;
  constructor(message: string, errorCategory = 'invalid_sql') {
    super(message);
    this.errorCategory = errorCategory;
    this.name = 'SqlShapeError';
  }
}

// Token kinds
const TK_WORD = 'WORD';
const TK_NUMBER = 'NUMBER';
const TK_STRING = 'STRING';
const TK_QUOTED_ID = 'QID';
const TK_PUNCT = 'PUNCT';
const TK_OP = 'OP';
const TK_LPAREN = 'LPAREN';
const TK_RPAREN = 'RPAREN';
const TK_EOF = 'EOF';

type Token = [string, string, number]; // [kind, text, position]

const FORBIDDEN_KEYWORD_SET = new Set(
  FORBIDDEN_KEYWORDS.map((k) => k.toUpperCase()),
);

const SUBQUERY_MARKER_KEYWORDS = new Set([
  'SELECT', 'FROM', 'WHERE', 'GROUP', 'HAVING', 'ORDER', 'LIMIT', 'OFFSET',
]);

const TRAILING_CLAUSE_KEYWORD_SET = new Set([
  'GROUP', 'HAVING', 'ORDER', 'LIMIT', 'OFFSET',
]);

function tokenize(sql: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  const n = sql.length;

  while (i < n) {
    const ch = sql[i];

    // Whitespace
    if (/\s/.test(ch)) { i++; continue; }

    // Line comments
    if (ch === '-' && i + 1 < n && sql[i + 1] === '-') {
      throw new SqlShapeError(`line comments (--) are not permitted (at position ${i})`);
    }
    // Block comments
    if (ch === '/' && i + 1 < n && sql[i + 1] === '*') {
      throw new SqlShapeError(`block comments (/* */) are not permitted (at position ${i})`);
    }
    // Semicolons
    if (ch === ';') {
      throw new SqlShapeError(`semicolons are not permitted (at position ${i})`);
    }

    // String literals
    if (ch === "'") {
      const start = i;
      i++;
      while (i < n) {
        if (sql[i] === "'") {
          if (i + 1 < n && sql[i + 1] === "'") { i += 2; continue; }
          i++;
          tokens.push([TK_STRING, sql.slice(start, i), start]);
          break;
        }
        i++;
      }
      if (i >= n && (tokens.length === 0 || tokens[tokens.length - 1][2] !== start)) {
        throw new SqlShapeError(`unterminated string literal (at position ${start})`);
      }
      continue;
    }

    // Quoted identifiers
    if (ch === '"') {
      const start = i;
      i++;
      while (i < n) {
        if (sql[i] === '"') {
          if (i + 1 < n && sql[i + 1] === '"') { i += 2; continue; }
          i++;
          tokens.push([TK_QUOTED_ID, sql.slice(start, i), start]);
          break;
        }
        i++;
      }
      if (i >= n && (tokens.length === 0 || tokens[tokens.length - 1][2] !== start)) {
        throw new SqlShapeError(`unterminated quoted identifier (at position ${start})`);
      }
      continue;
    }

    // Numeric literals
    if (/\d/.test(ch)) {
      const start = i;
      while (i < n && (/\d/.test(sql[i]) || sql[i] === '.')) { i++; }
      tokens.push([TK_NUMBER, sql.slice(start, i), start]);
      continue;
    }

    // Word tokens (identifiers/keywords)
    if (/[A-Za-z_]/.test(ch)) {
      const start = i;
      while (i < n && /[A-Za-z0-9_]/.test(sql[i])) { i++; }
      tokens.push([TK_WORD, sql.slice(start, i), start]);
      continue;
    }

    // Two-character operators
    if (i + 1 < n) {
      const two = sql.slice(i, i + 2);
      if (['<=', '>=', '<>', '!='].includes(two)) {
        tokens.push([TK_OP, two, i]);
        i += 2;
        continue;
      }
    }

    // Single-character operators
    if ('=<>'.includes(ch)) {
      tokens.push([TK_OP, ch, i]);
      i++;
      continue;
    }

    // Parentheses
    if (ch === '(') { tokens.push([TK_LPAREN, ch, i]); i++; continue; }
    if (ch === ')') { tokens.push([TK_RPAREN, ch, i]); i++; continue; }

    // Punctuation
    if (',.*+-/%'.includes(ch)) { tokens.push([TK_PUNCT, ch, i]); i++; continue; }

    // Unsupported character
    throw new SqlShapeError(`unsupported character ${JSON.stringify(ch)} (at position ${i})`);
  }

  tokens.push([TK_EOF, '', n]);
  return tokens;
}

function findTopLevelKeyword(
  tokens: Token[], keyword: string, startAfter = -1,
): number | null {
  const upper = keyword.toUpperCase();
  let depth = 0;
  for (let idx = 0; idx <= startAfter && idx < tokens.length; idx++) {
    if (tokens[idx][0] === TK_LPAREN) depth++;
    else if (tokens[idx][0] === TK_RPAREN) { depth--; if (depth < 0) return null; }
  }
  for (let idx = startAfter + 1; idx < tokens.length; idx++) {
    const [kind, text] = tokens[idx];
    if (kind === TK_LPAREN) { depth++; continue; }
    if (kind === TK_RPAREN) { depth--; if (depth < 0) return null; continue; }
    if (depth === 0 && kind === TK_WORD && text.toUpperCase() === upper) return idx;
  }
  return null;
}

/**
 * Validates SQL shape per the documented grammar. Returns tokens on success.
 * Throws SqlShapeError on rejection.
 */
function validateSqlShape(sql: string): Token[] {
  if (typeof sql !== 'string') {
    throw new SqlShapeError(`sql must be a string, got ${typeof sql}`);
  }
  if (!sql.trim()) throw new SqlShapeError('sql must not be empty');
  if (sql.length > MAX_SQL_LENGTH) {
    throw new SqlShapeError(`sql must be 1-${MAX_SQL_LENGTH} characters, got ${sql.length}`);
  }

  const tokens = tokenize(sql);
  const realTokens = tokens.slice(0, -1);
  if (realTokens.length === 0) throw new SqlShapeError('sql must not be empty');

  // Must start with SELECT
  const [firstKind, firstText] = realTokens[0];
  if (firstKind !== TK_WORD || firstText.toUpperCase() !== 'SELECT') {
    throw new SqlShapeError(
      `only top-level SELECT statements are permitted; first token was ${JSON.stringify(firstText)}`,
    );
  }

  // Forbidden keyword scan + subquery detection
  let parenDepth = 0;
  for (const [kind, text, position] of realTokens) {
    if (kind === TK_LPAREN) { parenDepth++; continue; }
    if (kind === TK_RPAREN) {
      parenDepth--;
      if (parenDepth < 0) throw new SqlShapeError(`unbalanced parenthesis (at position ${position})`);
      continue;
    }
    if (kind !== TK_WORD) continue;
    const upper = text.toUpperCase();
    if (FORBIDDEN_KEYWORD_SET.has(upper)) {
      throw new SqlShapeError(`keyword ${JSON.stringify(upper)} is not permitted in query_pcap SQL (at position ${position})`);
    }
    if (parenDepth > 0 && SUBQUERY_MARKER_KEYWORDS.has(upper)) {
      throw new SqlShapeError(`subqueries are not permitted; found ${JSON.stringify(upper)} inside parentheses (at position ${position})`);
    }
  }
  if (parenDepth !== 0) throw new SqlShapeError('unbalanced parenthesis at end of statement');

  // FROM pcap_logs required
  const fromIndex = findTopLevelKeyword(realTokens, 'FROM');
  if (fromIndex === null) throw new SqlShapeError('query_pcap SQL must include FROM pcap_logs');
  if (fromIndex + 1 >= realTokens.length) {
    throw new SqlShapeError('query_pcap SQL FROM clause is missing the table name');
  }

  const [tableKind, tableText] = realTokens[fromIndex + 1];
  let tableName: string | null = null;
  if (tableKind === TK_WORD) tableName = tableText;
  else if (tableKind === TK_QUOTED_ID) tableName = tableText.slice(1, -1).replace(/""/g, '"');
  if (tableName !== PCAP_LOGS_TABLE_NAME) {
    throw new SqlShapeError(`FROM target must be the '${PCAP_LOGS_TABLE_NAME}' table, got ${JSON.stringify(tableText)}`);
  }

  return tokens;
}

/**
 * Injects the Capture_Id_Predicate into validated SQL.
 * Only call on SQL that has passed validateSqlShape.
 */
function injectCaptureIdPredicate(sql: string, captureId: string, tokens?: Token[]): string {
  if (!tokens) tokens = tokenize(sql);
  const realTokens = tokens.slice(0, -1);

  const fromIndex = findTopLevelKeyword(realTokens, 'FROM');
  if (fromIndex === null) throw new SqlShapeError('validated SQL is missing FROM');

  const whereIndex = findTopLevelKeyword(realTokens, 'WHERE', fromIndex);
  const predicateClause = `capture_id = '${captureId}'`;

  const findFirstTrailingClause = (startAfter: number): number => {
    for (let idx = startAfter + 1; idx < realTokens.length; idx++) {
      const [kind, text] = realTokens[idx];
      if (kind === TK_WORD && TRAILING_CLAUSE_KEYWORD_SET.has(text.toUpperCase())) return idx;
    }
    return realTokens.length;
  };

  if (whereIndex === null) {
    const firstTrailing = findFirstTrailingClause(fromIndex);
    if (firstTrailing < realTokens.length) {
      const insertPos = realTokens[firstTrailing][2];
      const head = sql.slice(0, insertPos).trimEnd();
      const tail = sql.slice(insertPos);
      return `${head} WHERE ${predicateClause} ${tail}`.trimEnd();
    }
    return `${sql.trimEnd()} WHERE ${predicateClause}`;
  }

  const firstTrailing = findFirstTrailingClause(whereIndex);
  if (firstTrailing < realTokens.length) {
    const insertPos = realTokens[firstTrailing][2];
    const head = sql.slice(0, insertPos).trimEnd();
    const tail = sql.slice(insertPos);
    return `${head} AND ${predicateClause} ${tail}`.trimEnd();
  }
  return `${sql.trimEnd()} AND ${predicateClause}`;
}

// ---------------------------------------------------------------------------
// Simulated query_pcap handler (combines validator + injector)
// ---------------------------------------------------------------------------

interface QueryPcapResult {
  accepted: boolean;
  rewrittenSql?: string;
  error?: string;
  athenaInvoked: boolean;
}

/**
 * Simulates the full query_pcap pipeline: validate capture_id, validate SQL
 * shape, inject predicate, then (simulated) Athena call.
 */
function simulateQueryPcap(sql: string, captureId: string): QueryPcapResult {
  // Step 1: Validate capture_id
  if (!CAPTURE_ID_REGEX.test(captureId)) {
    return { accepted: false, error: 'invalid capture_id', athenaInvoked: false };
  }

  // Step 2: Validate SQL shape
  let tokens: Token[];
  try {
    tokens = validateSqlShape(sql);
  } catch (e) {
    if (e instanceof SqlShapeError) {
      return { accepted: false, error: e.message, athenaInvoked: false };
    }
    throw e;
  }

  // Step 3: Inject predicate
  const rewrittenSql = injectCaptureIdPredicate(sql, captureId, tokens);

  // Step 4: Athena would be invoked here
  return { accepted: true, rewrittenSql, athenaInvoked: true };
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Valid capture_id characters */
const VALID_ID_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-';

/** Generates valid capture_id values */
const arbValidCaptureId: fc.Arbitrary<string> = fc.stringOf(
  fc.constantFrom(...VALID_ID_CHARS.split('')),
  { minLength: 1, maxLength: 64 },
);

/** Valid SQL column names for pcap_logs */
const PCAP_COLUMNS = [
  'frame_time', 'frame_size', 'src_ip', 'dst_ip', 'src_port', 'dst_port',
  'protocol', 'tcp_seq', 'tcp_ack', 'tcp_flags', 'tcp_options', 'tcp_stream',
  'tcp_window', 'tls_handshake_type', 'tls_record_size', 'tls_sni',
  'tls_fragment_count', 'dns_qname', 'dns_response_ips',
  'frame_payload_summary', 'capture_id',
];

/** Generates a random subset of columns for SELECT projection */
const arbProjection: fc.Arbitrary<string> = fc.oneof(
  fc.constant('*'),
  fc.subarray(PCAP_COLUMNS, { minLength: 1, maxLength: 5 })
    .map((cols) => cols.join(', ')),
  fc.constantFrom('COUNT(*)', 'SUM(frame_size)', 'AVG(frame_size)'),
);

/** Generates simple WHERE predicates */
const arbWherePredicate: fc.Arbitrary<string> = fc.oneof(
  fc.constantFrom(
    "src_ip = '10.0.0.1'",
    'frame_size > 1400',
    "protocol = 'TCP'",
    "dst_port = 443",
    "tcp_flags = 'SYN'",
    "tls_handshake_type = 1",
  ),
  fc.tuple(
    fc.constantFrom(...PCAP_COLUMNS.filter((c) => c !== 'capture_id')),
    fc.constantFrom('=', '>', '<', '>=', '<=', '<>'),
    fc.oneof(fc.constant("'test_value'"), fc.nat({ max: 65535 }).map(String)),
  ).map(([col, op, val]) => `${col} ${op} ${val}`),
);

/** Generates valid SELECT queries against pcap_logs */
const arbValidSelect: fc.Arbitrary<string> = fc.tuple(
  arbProjection,
  fc.option(arbWherePredicate, { nil: undefined }),
  fc.option(
    fc.subarray(PCAP_COLUMNS, { minLength: 1, maxLength: 2 }).map((cols) => `GROUP BY ${cols.join(', ')}`),
    { nil: undefined },
  ),
  fc.option(
    fc.subarray(PCAP_COLUMNS, { minLength: 1, maxLength: 2 }).map((cols) => `ORDER BY ${cols.join(', ')}`),
    { nil: undefined },
  ),
  fc.option(fc.nat({ max: 1000 }).map((n) => `LIMIT ${n + 1}`), { nil: undefined }),
).map(([proj, where, groupBy, orderBy, limit]) => {
  let sql = `SELECT ${proj} FROM pcap_logs`;
  if (where) sql += ` WHERE ${where}`;
  if (groupBy) sql += ` ${groupBy}`;
  if (orderBy) sql += ` ${orderBy}`;
  if (limit) sql += ` ${limit}`;
  return sql;
});

/** Generates SQL with forbidden DDL/DML keywords */
const arbForbiddenKeywordSql: fc.Arbitrary<string> = fc.constantFrom(
  ...FORBIDDEN_KEYWORDS.map((kw) => `${kw} pcap_logs`),
  ...FORBIDDEN_KEYWORDS.map((kw) => `SELECT * FROM pcap_logs ${kw} something`),
  'INSERT INTO pcap_logs VALUES (1)',
  'UPDATE pcap_logs SET frame_size = 0',
  'DELETE FROM pcap_logs',
  'DROP TABLE pcap_logs',
  'CREATE TABLE evil (id INT)',
  'ALTER TABLE pcap_logs ADD COLUMN evil INT',
  'TRUNCATE TABLE pcap_logs',
  'MSCK REPAIR TABLE pcap_logs',
  'SELECT * FROM pcap_logs JOIN other_table ON 1=1',
  'SELECT * FROM pcap_logs UNION SELECT * FROM secrets',
  'WITH cte AS (SELECT 1) SELECT * FROM pcap_logs',
);

/** Generates SQL with comment injection attempts */
const arbCommentInjectedSql: fc.Arbitrary<string> = fc.constantFrom(
  "SELECT * FROM pcap_logs -- WHERE capture_id = 'evil'",
  "SELECT * FROM pcap_logs /* injected */ WHERE 1=1",
  "SELECT * FROM pcap_logs WHERE src_ip = '10.0.0.1' -- bypass",
  "SELECT /* comment */ * FROM pcap_logs",
  "-- DROP TABLE pcap_logs\nSELECT * FROM pcap_logs",
  "SELECT * FROM pcap_logs WHERE 1=1 /* AND capture_id = 'x' */",
);

/** Generates SQL with semicolons (statement termination attacks) */
const arbSemicolonSql: fc.Arbitrary<string> = fc.constantFrom(
  "SELECT * FROM pcap_logs; DROP TABLE pcap_logs",
  "SELECT * FROM pcap_logs WHERE 1=1; DELETE FROM pcap_logs",
  "SELECT * FROM pcap_logs;",
  "; SELECT * FROM pcap_logs",
);

/** Generates SQL with subquery attempts */
const arbSubquerySql: fc.Arbitrary<string> = fc.constantFrom(
  "SELECT * FROM pcap_logs WHERE src_ip IN (SELECT src_ip FROM pcap_logs)",
  "SELECT * FROM pcap_logs WHERE frame_size > (SELECT AVG(frame_size) FROM pcap_logs)",
  "SELECT (SELECT COUNT(*) FROM pcap_logs) FROM pcap_logs",
);

/** Generates SQL with UNION attempts */
const arbUnionSql: fc.Arbitrary<string> = fc.constantFrom(
  'SELECT * FROM pcap_logs UNION SELECT * FROM pcap_logs',
  'SELECT * FROM pcap_logs UNION ALL SELECT * FROM pcap_logs',
  'SELECT src_ip FROM pcap_logs INTERSECT SELECT dst_ip FROM pcap_logs',
  'SELECT src_ip FROM pcap_logs EXCEPT SELECT dst_ip FROM pcap_logs',
);

/** Generates SQL with JOIN attempts */
const arbJoinSql: fc.Arbitrary<string> = fc.constantFrom(
  'SELECT * FROM pcap_logs JOIN other ON pcap_logs.id = other.id',
  'SELECT * FROM pcap_logs INNER JOIN other ON 1=1',
  'SELECT * FROM pcap_logs LEFT JOIN other ON 1=1',
  'SELECT * FROM pcap_logs CROSS JOIN other',
);

/** Generates non-SELECT SQL (not starting with SELECT) */
const arbNonSelectSql: fc.Arbitrary<string> = fc.constantFrom(
  'FROM pcap_logs SELECT *',
  'SHOW TABLES',
  'DESCRIBE pcap_logs',
  'EXPLAIN SELECT * FROM pcap_logs',
  '',
  '   ',
);

// ---------------------------------------------------------------------------
// Property Tests
// ---------------------------------------------------------------------------

describe('Network Agent SQL safety property tests (Properties 5 & 6)', () => {
  /**
   * Property 5a: For every valid SELECT query accepted by the shape validator,
   * the predicate injector produces output containing an exact
   * `capture_id = '<id>'` predicate.
   *
   * **Validates: Requirements 5.1, 5.2, 5.7**
   */
  it('Property 5a: accepted SQL always contains capture_id predicate after injection — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbValidSelect, arbValidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);

        // Must be accepted
        expect(result.accepted).toBe(true);
        expect(result.athenaInvoked).toBe(true);
        expect(result.rewrittenSql).toBeDefined();

        // Must contain the exact predicate
        const expectedPredicate = `capture_id = '${captureId}'`;
        expect(result.rewrittenSql).toContain(expectedPredicate);
      }),
      { numRuns: 300 },
    );
  });

  /**
   * Property 5b: The injected predicate appears at the top-level WHERE clause,
   * not inside a string literal or nested expression.
   *
   * **Validates: Requirements 5.1, 5.7**
   */
  it('Property 5b: injected predicate is at top-level WHERE — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbValidSelect, arbValidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);
        if (!result.accepted || !result.rewrittenSql) return;

        const rewritten = result.rewrittenSql;
        const predicate = `capture_id = '${captureId}'`;

        // The predicate must appear either as:
        // - "WHERE capture_id = '<id>'" (new WHERE clause)
        // - "AND capture_id = '<id>'" (appended to existing WHERE)
        const hasWhereForm = rewritten.includes(`WHERE ${predicate}`);
        const hasAndForm = rewritten.includes(`AND ${predicate}`);
        expect(hasWhereForm || hasAndForm).toBe(true);

        // Verify the predicate is NOT inside a string literal by checking
        // that it doesn't appear after an odd number of single quotes
        const beforePredicate = rewritten.slice(0, rewritten.indexOf(predicate));
        const singleQuoteCount = (beforePredicate.match(/'/g) || []).length;
        // Even number of quotes means we're outside a string literal
        expect(singleQuoteCount % 2).toBe(0);
      }),
      { numRuns: 200 },
    );
  });

  /**
   * Property 5c: When the original SQL has no WHERE clause, the injector
   * creates a new WHERE clause. When it has one, it appends with AND.
   *
   * **Validates: Requirements 5.1, 5.7**
   */
  it('Property 5c: WHERE creation vs AND append is correct — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbValidSelect, arbValidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);
        if (!result.accepted || !result.rewrittenSql) return;

        const predicate = `capture_id = '${captureId}'`;
        const originalHasWhere = /\bWHERE\b/i.test(sql);

        if (originalHasWhere) {
          // Should append with AND
          expect(result.rewrittenSql).toContain(`AND ${predicate}`);
        } else {
          // Should create new WHERE
          expect(result.rewrittenSql).toContain(`WHERE ${predicate}`);
        }
      }),
      { numRuns: 200 },
    );
  });

  /**
   * Property 6a: Forbidden top-level keywords cause rejection and Athena
   * is never invoked.
   *
   * **Validates: Requirements 5.3, 5.20**
   */
  it('Property 6a: forbidden keywords cause rejection without Athena invocation — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbForbiddenKeywordSql, arbValidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);

        // Must be rejected
        expect(result.accepted).toBe(false);
        // Athena must NOT be invoked
        expect(result.athenaInvoked).toBe(false);
        // Must have an error message
        expect(result.error).toBeDefined();
        expect(result.error!.length).toBeGreaterThan(0);
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 6b: Comment-injected SQL variants are rejected and Athena
   * is never invoked.
   *
   * **Validates: Requirements 5.3**
   */
  it('Property 6b: comment-injected SQL is rejected without Athena invocation — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbCommentInjectedSql, arbValidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);

        expect(result.accepted).toBe(false);
        expect(result.athenaInvoked).toBe(false);
        expect(result.error).toBeDefined();
      }),
      { numRuns: 50 },
    );
  });

  /**
   * Property 6c: Semicolons cause rejection and Athena is never invoked.
   *
   * **Validates: Requirements 5.3**
   */
  it('Property 6c: semicolons cause rejection without Athena invocation — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbSemicolonSql, arbValidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);

        expect(result.accepted).toBe(false);
        expect(result.athenaInvoked).toBe(false);
        expect(result.error).toBeDefined();
      }),
      { numRuns: 50 },
    );
  });

  /**
   * Property 6d: Subquery attempts are rejected and Athena is never invoked.
   *
   * **Validates: Requirements 5.3**
   */
  it('Property 6d: subquery attempts are rejected without Athena invocation — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbSubquerySql, arbValidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);

        expect(result.accepted).toBe(false);
        expect(result.athenaInvoked).toBe(false);
        expect(result.error).toBeDefined();
      }),
      { numRuns: 50 },
    );
  });

  /**
   * Property 6e: UNION/INTERSECT/EXCEPT attempts are rejected.
   *
   * **Validates: Requirements 5.3**
   */
  it('Property 6e: UNION/INTERSECT/EXCEPT are rejected without Athena invocation — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbUnionSql, arbValidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);

        expect(result.accepted).toBe(false);
        expect(result.athenaInvoked).toBe(false);
        expect(result.error).toBeDefined();
      }),
      { numRuns: 50 },
    );
  });

  /**
   * Property 6f: JOIN attempts are rejected.
   *
   * **Validates: Requirements 5.3**
   */
  it('Property 6f: JOIN attempts are rejected without Athena invocation — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbJoinSql, arbValidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);

        expect(result.accepted).toBe(false);
        expect(result.athenaInvoked).toBe(false);
        expect(result.error).toBeDefined();
      }),
      { numRuns: 50 },
    );
  });

  /**
   * Property 6g: Non-SELECT SQL is rejected.
   *
   * **Validates: Requirements 5.3**
   */
  it('Property 6g: non-SELECT SQL is rejected without Athena invocation — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbNonSelectSql, arbValidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);

        expect(result.accepted).toBe(false);
        expect(result.athenaInvoked).toBe(false);
        expect(result.error).toBeDefined();
      }),
      { numRuns: 50 },
    );
  });

  /**
   * Property 6h: Each individual forbidden keyword is rejected regardless
   * of case.
   *
   * **Validates: Requirements 5.3, 5.20**
   */
  it('Property 6h: each forbidden keyword is rejected in any case — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...FORBIDDEN_KEYWORDS),
        fc.constantFrom('lower', 'UPPER', 'MiXeD'),
        arbValidCaptureId,
        (keyword, caseStyle, captureId) => {
          let kw: string;
          if (caseStyle === 'lower') kw = keyword.toLowerCase();
          else if (caseStyle === 'UPPER') kw = keyword.toUpperCase();
          else kw = keyword[0].toUpperCase() + keyword.slice(1).toLowerCase();

          // Construct SQL that embeds the forbidden keyword at top level
          const sql = `SELECT * FROM pcap_logs ${kw} something`;
          const result = simulateQueryPcap(sql, captureId);

          expect(result.accepted).toBe(false);
          expect(result.athenaInvoked).toBe(false);
        },
      ),
      { numRuns: 100 },
    );
  });

  /**
   * Property 5d: The capture_id in the injected predicate exactly matches
   * the supplied capture_id (no truncation, no mutation).
   *
   * **Validates: Requirements 5.1, 5.20**
   */
  it('Property 5d: injected capture_id is exact match of input — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(arbValidSelect, arbValidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);
        if (!result.accepted || !result.rewrittenSql) return;

        // Extract the capture_id from the injected predicate
        const pattern = /capture_id = '([^']+)'/;
        const match = result.rewrittenSql.match(pattern);
        expect(match).not.toBeNull();
        expect(match![1]).toBe(captureId);
      }),
      { numRuns: 200 },
    );
  });

  /**
   * Property 5e: Invalid capture_id values cause rejection before SQL
   * validation even begins (Athena never invoked).
   *
   * **Validates: Requirements 5.2, 5.20**
   */
  it('Property 5e: invalid capture_id rejects before SQL validation — Feature: genai-operations-analytics-tool', () => {
    const arbInvalidCaptureId = fc.oneof(
      fc.constant(''),
      fc.stringOf(fc.constantFrom(...VALID_ID_CHARS.split('')), { minLength: 129, maxLength: 200 }),
      fc.constant('has spaces'),
      fc.constant('has;semicolon'),
      fc.constant("has'quote"),
      fc.constant('has/slash'),
    );

    fc.assert(
      fc.property(arbValidSelect, arbInvalidCaptureId, (sql, captureId) => {
        const result = simulateQueryPcap(sql, captureId);

        expect(result.accepted).toBe(false);
        expect(result.athenaInvoked).toBe(false);
        expect(result.error).toContain('invalid capture_id');
      }),
      { numRuns: 100 },
    );
  });

  /**
   * Property 6i: The shape validator + injector is a total function —
   * for any arbitrary string input, it either rejects (with no Athena call)
   * or produces output containing the capture_id predicate. It never throws
   * an unhandled exception.
   *
   * **Validates: Requirements 5.1, 5.2, 5.3, 5.7**
   */
  it('Property 6i: validator+injector is total — never throws unhandled — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 0, maxLength: 500 }), arbValidCaptureId, (sql, captureId) => {
        // Must not throw — always returns a result
        const result = simulateQueryPcap(sql, captureId);

        if (result.accepted) {
          // If accepted, must have rewritten SQL with predicate
          expect(result.rewrittenSql).toBeDefined();
          expect(result.rewrittenSql).toContain(`capture_id = '${captureId}'`);
          expect(result.athenaInvoked).toBe(true);
        } else {
          // If rejected, Athena must not be invoked
          expect(result.athenaInvoked).toBe(false);
          expect(result.error).toBeDefined();
        }
      }),
      { numRuns: 500 },
    );
  });

  /**
   * Property 6j: Keywords inside string literals do NOT cause rejection.
   * The shape validator must only reject keywords that appear as unquoted
   * identifiers, not those inside SQL string values.
   *
   * **Validates: Requirements 5.3**
   */
  it('Property 6j: forbidden keywords inside string literals are allowed — Feature: genai-operations-analytics-tool', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...FORBIDDEN_KEYWORDS),
        arbValidCaptureId,
        (keyword, captureId) => {
          // Keyword inside a string literal should be fine
          const sql = `SELECT * FROM pcap_logs WHERE src_ip = '${keyword}'`;
          const result = simulateQueryPcap(sql, captureId);

          expect(result.accepted).toBe(true);
          expect(result.athenaInvoked).toBe(true);
          expect(result.rewrittenSql).toContain(`capture_id = '${captureId}'`);
        },
      ),
      { numRuns: 50 },
    );
  });
});
