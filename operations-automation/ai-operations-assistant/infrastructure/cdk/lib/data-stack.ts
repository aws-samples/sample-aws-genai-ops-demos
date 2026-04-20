import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';

/**
 * G.O.A.T. DataStack — DynamoDB tables for conversations, knowledge articles,
 * and user preferences.
 */
export class DataStack extends cdk.Stack {
  public readonly conversationsTable: dynamodb.Table;
  public readonly knowledgeArticlesTable: dynamodb.Table;
  public readonly userPreferencesTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // -----------------------------------------------------------------------
    // Conversations Table  (PK: USER#<userId>, SK: CONV#<conversationId>)
    // TTL enabled for 90-day archival
    // -----------------------------------------------------------------------
    this.conversationsTable = new dynamodb.Table(this, 'ConversationsTable', {
      tableName: `goat-conversations-${this.account}-${this.region}`,
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      timeToLiveAttribute: 'TTL',
    });

    // -----------------------------------------------------------------------
    // Knowledge Articles Table  (PK: ARTICLE#<articleId>, SK: META)
    // GSI1: CATEGORY#<category> / <createdAt> for category-based queries
    // -----------------------------------------------------------------------
    this.knowledgeArticlesTable = new dynamodb.Table(this, 'KnowledgeArticlesTable', {
      tableName: `goat-knowledge-articles-${this.account}-${this.region}`,
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.knowledgeArticlesTable.addGlobalSecondaryIndex({
      indexName: 'GSI1',
      partitionKey: { name: 'GSI1PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'GSI1SK', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // -----------------------------------------------------------------------
    // User Preferences Table  (PK: USER#<userId>, SK: PREFS)
    // -----------------------------------------------------------------------
    this.userPreferencesTable = new dynamodb.Table(this, 'UserPreferencesTable', {
      tableName: `goat-user-preferences-${this.account}-${this.region}`,
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // -----------------------------------------------------------------------
    // Stack Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'ConversationsTableName', {
      value: this.conversationsTable.tableName,
      description: 'DynamoDB Conversations table name',
      exportName: 'GOATConversationsTableName',
    });

    new cdk.CfnOutput(this, 'ConversationsTableArn', {
      value: this.conversationsTable.tableArn,
      description: 'DynamoDB Conversations table ARN',
      exportName: 'GOATConversationsTableArn',
    });

    new cdk.CfnOutput(this, 'KnowledgeArticlesTableName', {
      value: this.knowledgeArticlesTable.tableName,
      description: 'DynamoDB Knowledge Articles table name',
      exportName: 'GOATKnowledgeArticlesTableName',
    });

    new cdk.CfnOutput(this, 'KnowledgeArticlesTableArn', {
      value: this.knowledgeArticlesTable.tableArn,
      description: 'DynamoDB Knowledge Articles table ARN',
      exportName: 'GOATKnowledgeArticlesTableArn',
    });

    new cdk.CfnOutput(this, 'UserPreferencesTableName', {
      value: this.userPreferencesTable.tableName,
      description: 'DynamoDB User Preferences table name',
      exportName: 'GOATUserPreferencesTableName',
    });

    new cdk.CfnOutput(this, 'UserPreferencesTableArn', {
      value: this.userPreferencesTable.tableArn,
      description: 'DynamoDB User Preferences table ARN',
      exportName: 'GOATUserPreferencesTableArn',
    });
  }
}
