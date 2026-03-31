/**
 * Structured Logging Utility for Merchant Gateway
 * Requirements: 10.5
 * 
 * Provides JSON-formatted structured logging for CloudWatch Logs aggregation.
 * All log entries include standard fields for correlation, service identification,
 * and timestamp for easy querying with CloudWatch Logs Insights.
 */

export enum LogLevel {
  DEBUG = 'debug',
  INFO = 'info',
  WARN = 'warn',
  ERROR = 'error',
}

export interface LogContext {
  correlationId?: string;
  merchantId?: string;
  transactionId?: string;
  operation?: string;
  endpoint?: string;
  [key: string]: unknown;
}

export interface LogEntry {
  timestamp: string;
  level: LogLevel;
  service: string;
  environment: string;
  message: string;
  correlationId?: string;
  merchantId?: string;
  transactionId?: string;
  operation?: string;
  endpoint?: string;
  responseTime?: number;
  statusCode?: number;
  errorCode?: string;
  errorMessage?: string;
  stack?: string;
  [key: string]: unknown;
}

const SERVICE_NAME = process.env.XRAY_SERVICE_NAME || 'merchant-gateway';
const ENVIRONMENT = process.env.ENVIRONMENT || process.env.NODE_ENV || 'dev';

/**
 * Create a structured log entry
 */
function createLogEntry(
  level: LogLevel,
  message: string,
  context?: LogContext,
  error?: Error
): LogEntry {
  const entry: LogEntry = {
    timestamp: new Date().toISOString(),
    level,
    service: SERVICE_NAME,
    environment: ENVIRONMENT,
    message,
    ...context,
  };

  if (error) {
    entry.errorCode = (error as any).code || 'UNKNOWN_ERROR';
    entry.errorMessage = error.message;
    entry.stack = error.stack;
  }

  return entry;
}

/**
 * Output log entry to console in JSON format
 */
function outputLog(entry: LogEntry): void {
  const output = JSON.stringify(entry);
  
  switch (entry.level) {
    case LogLevel.ERROR:
      console.error(output);
      break;
    case LogLevel.WARN:
      console.warn(output);
      break;
    case LogLevel.DEBUG:
      console.debug(output);
      break;
    default:
      console.log(output);
  }
}

/**
 * Logger class for structured logging
 */
export class Logger {
  private context: LogContext;

  constructor(context?: LogContext) {
    this.context = context || {};
  }

  /**
   * Create a child logger with additional context
   */
  child(additionalContext: LogContext): Logger {
    return new Logger({ ...this.context, ...additionalContext });
  }

  /**
   * Log debug message
   */
  debug(message: string, data?: Record<string, unknown>): void {
    if (ENVIRONMENT === 'prod') return; // Skip debug logs in production
    const entry = createLogEntry(LogLevel.DEBUG, message, { ...this.context, ...data });
    outputLog(entry);
  }

  /**
   * Log info message
   */
  info(message: string, data?: Record<string, unknown>): void {
    const entry = createLogEntry(LogLevel.INFO, message, { ...this.context, ...data });
    outputLog(entry);
  }

  /**
   * Log warning message
   */
  warn(message: string, data?: Record<string, unknown>): void {
    const entry = createLogEntry(LogLevel.WARN, message, { ...this.context, ...data });
    outputLog(entry);
  }

  /**
   * Log error message
   */
  error(message: string, error?: Error, data?: Record<string, unknown>): void {
    const entry = createLogEntry(LogLevel.ERROR, message, { ...this.context, ...data }, error);
    outputLog(entry);
  }

  /**
   * Log HTTP request
   */
  logRequest(
    method: string,
    path: string,
    statusCode: number,
    responseTime: number,
    data?: Record<string, unknown>
  ): void {
    const entry = createLogEntry(LogLevel.INFO, `${method} ${path}`, {
      ...this.context,
      operation: 'http_request',
      endpoint: path,
      statusCode,
      responseTime,
      httpMethod: method,
      ...data,
    });
    outputLog(entry);
  }

  /**
   * Log authentication event
   */
  logAuth(
    result: 'success' | 'failure',
    merchantId?: string,
    errorCode?: string,
    data?: Record<string, unknown>
  ): void {
    const entry = createLogEntry(
      result === 'success' ? LogLevel.INFO : LogLevel.WARN,
      `Authentication ${result}`,
      {
        ...this.context,
        operation: 'authenticate',
        authResult: result,
        merchantId,
        errorCode,
        ...data,
      }
    );
    outputLog(entry);
  }

  /**
   * Log rate limiting event
   */
  logRateLimit(
    merchantId: string,
    requestCount: number,
    exceeded: boolean,
    data?: Record<string, unknown>
  ): void {
    const entry = createLogEntry(
      exceeded ? LogLevel.WARN : LogLevel.DEBUG,
      exceeded ? 'Rate limit exceeded' : 'Rate limit check',
      {
        ...this.context,
        operation: 'rate_limit',
        merchantId,
        requestCount,
        rateLimitExceeded: exceeded,
        ...data,
      }
    );
    outputLog(entry);
  }
}

/**
 * Default logger instance
 */
export const logger = new Logger();

/**
 * Create a request-scoped logger with correlation ID
 */
export function createRequestLogger(correlationId: string, merchantId?: string): Logger {
  return new Logger({ correlationId, merchantId });
}
