// ============================================================
// SAMPLE INPUTS — pre-loaded examples for demonstration
// ============================================================

export const SAMPLES = {
  mermaid: [
    {
      label: 'Demo: S3 → Step Functions',
      content: `flowchart LR
  S3[Document Bucket] --> SFN[Processing State Machine]
  SFN --> L[Extract Lambda]
  L --> DDB[(Results Table)]
  L --> SNS[Alert Topic]
  L --> EB[Event Bus]
  SNS --> Q[Notify Queue]
  EB --> Q`,
    },
    {
      label: 'API → Lambda → DDB',
      content: `graph TD
  APIGW[API Gateway] --> FN[Lambda Function]
  FN --> DB[(DynamoDB Table)]
  FN --> Q[SQS Queue]
  Q --> Worker[Worker Lambda]`,
    },
    {
      label: 'Event-driven pipeline',
      content: `flowchart LR
  bucket[S3 Bucket] --> eb[EventBridge]
  eb --> sfn[Step Functions]
  sfn --> proc[Lambda Processor]
  proc --> firehose[Data Firehose]
  firehose --> output[S3 Archive]`,
    },
  ],

  template: [
    {
      label: 'Self-audit Precedent stack',
      content: `AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31

Resources:
  HttpApi:
    Type: AWS::Serverless::HttpApi

  AuditFunction:
    Type: AWS::Serverless::Function
    Properties:
      Runtime: python3.12
      Handler: handler_audit.handler
      Events:
        Audit:
          Type: HttpApi
          Properties:
            ApiId: !Ref HttpApi
            Path: /audits
            Method: POST
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref AuditsTable

  AuditsTable:
    Type: AWS::DynamoDB::Table
    Properties:
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions:
        - AttributeName: audit_id
          AttributeType: S
      KeySchema:
        - AttributeName: audit_id
          KeyType: HASH`,
    },
    {
      label: 'S3 trigger with Lambda',
      content: `AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31

Resources:
  ProcessFunction:
    Type: AWS::Serverless::Function
    Properties:
      Runtime: python3.12
      Handler: app.handler
      Events:
        S3Event:
          Type: S3
          Properties:
            Bucket: !Ref UploadBucket
            Events: s3:ObjectCreated:*

  UploadBucket:
    Type: AWS::S3::Bucket`,
    },
  ],

  prose: [
    {
      label: 'Invoice processor',
      content:
        'Users upload scanned PDFs to an S3 bucket. A Lambda function triggered by each upload runs OCR to extract invoice data and writes structured records to DynamoDB. Failed extractions are sent to an SQS dead-letter queue.',
    },
    {
      label: 'Real-time analytics',
      content:
        'Events from IoT sensors are streamed to Kinesis Data Streams. A Lambda consumer reads the stream, aggregates metrics, and stores them in DynamoDB while publishing alerts to SNS when thresholds are breached.',
    },
  ],
};
