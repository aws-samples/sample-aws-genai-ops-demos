/**
 * Structured Logging Utility for Webhook Service
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
  webhookId?: string;
  operation?: string;
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
  webhookId?: string;
  operation?: string;
  deliveryAttempt?: number;
  responseTime?: number;
  statusCode?: number;
  errorCode?: string;
  errorMessage?: string;
  stack?: string;
  [key: string]: unknown;
}

const SERVICE_NAME = process.env.XRAY_SERVICE_NAME || 'webhook-service';
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
   * Log webhook delivery attempt
   */
  logWebhookDelivery(
    webhookId: string,
    merchantId: string,
    attempt: number,
    success: boolean,
    statusCode?: number,
    responseTime?: number,
    data?: Record<string, unknown>
  ): void {
    const entry = createLogEntry(
      success ? LogLevel.INFO : LogLevel.WARN,
      success ? 'Webhook delivered successfully' : 'Webhook delivery failed',
      {
        ...this.context,
        operation: 'webhook_delivery',
        webhookId,
        merchantId,
        deliveryAttempt: attempt,
        deliverySuccess: success,
        statusCode,
        responseTime,
        ...data,
      }
    );
    outputLog(entry);
  }

  /**
   * Log SQS message processing
   */
  logSQSMessage(
    messageId: string,
    eventType: string,
    transactionId?: string,
    data?: Record<string, unknown>
  ): void {
    const entry = createLogEntry(LogLevel.INFO, `Processing SQS message: ${eventType}`, {
      ...this.context,
      operation: 'sqs_message_processing',
      messageId,
      eventType,
      transactionId,
      ...data,
    });
    outputLog(entry);
  }

  /**
   * Log signature generation
   */
  logSignature(
    webhookId: string,
    merchantId: string,
    data?: Record<string, unknown>
  ): void {
    const entry = createLogEntry(LogLevel.DEBUG, 'Generated webhook signature', {
      ...this.context,
      operation: 'signature_generation',
      webhookId,
      merchantId,
      ...data,
    });
    outputLog(entry);
  }
}

/**
 * Default logger instance
 */
export const logger = new Logger();

/**
 * Create a webhook-scoped logger
 */
export function createWebhookLogger(webhookId: string, merchantId: string, transactionId?: string): Logger {
  return new Logger({ webhookId, merchantId, transactionId });
}
