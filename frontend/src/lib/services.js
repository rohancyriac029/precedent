// Mirror of backend/data/vocabulary.yaml: the 38 service ids the audit knows.
// Used for display names, drawing categories, and the service picker in the
// prose confirmation step. If the vocabulary changes, update this list.

export const CATEGORIES = {
  compute: 'Compute',
  edge: 'Edge & API',
  messaging: 'Messaging',
  storage: 'Storage',
  database: 'Database',
  analytics: 'Analytics',
  identity: 'Identity',
  ai: 'AI & ML',
  platform: 'Platform',
  unknown: 'Unmapped',
}

const S = (title, category) => ({ title, category })

export const SERVICES = {
  lambda: S('AWS Lambda', 'compute'),
  step_functions: S('Step Functions', 'compute'),
  ecs: S('Amazon ECS', 'compute'),
  fargate: S('AWS Fargate', 'compute'),

  apigateway: S('API Gateway', 'edge'),
  appsync: S('AWS AppSync', 'edge'),
  cloudfront: S('CloudFront', 'edge'),
  lambda_function_url: S('Function URL', 'edge'),
  alb: S('Load Balancer', 'edge'),

  sqs: S('Amazon SQS', 'messaging'),
  sns: S('Amazon SNS', 'messaging'),
  eventbridge: S('EventBridge', 'messaging'),
  eventbridge_scheduler: S('EB Scheduler', 'messaging'),
  kinesis_streams: S('Kinesis Streams', 'messaging'),
  firehose: S('Data Firehose', 'messaging'),
  amazonmq: S('Amazon MQ', 'messaging'),
  iot_core: S('IoT Core', 'messaging'),

  s3: S('Amazon S3', 'storage'),

  dynamodb: S('DynamoDB', 'database'),
  rds: S('Amazon RDS', 'database'),
  aurora: S('Aurora', 'database'),
  elasticache: S('ElastiCache', 'database'),
  opensearch: S('OpenSearch', 'database'),

  athena: S('Athena', 'analytics'),
  glue: S('AWS Glue', 'analytics'),

  cognito: S('Cognito', 'identity'),

  textract: S('Textract', 'ai'),
  rekognition: S('Rekognition', 'ai'),
  comprehend: S('Comprehend', 'ai'),
  bedrock: S('Bedrock', 'ai'),
  polly: S('Polly', 'ai'),
  translate: S('Translate', 'ai'),

  ses: S('Amazon SES', 'platform'),
  secrets_manager: S('Secrets Manager', 'platform'),
  ssm_parameter_store: S('Parameter Store', 'platform'),
  cloudwatch: S('CloudWatch', 'platform'),
  route53: S('Route 53', 'platform'),
  amplify: S('Amplify', 'platform'),
}

export function serviceTitle(id) {
  return SERVICES[id]?.title || (id === 'unknown' ? 'Unmapped' : id)
}

export function serviceCategory(id) {
  return SERVICES[id]?.category || 'unknown'
}

// Grouped options for the <select> in the confirmation step.
export const SERVICE_OPTIONS = Object.entries(CATEGORIES)
  .filter(([cat]) => cat !== 'unknown')
  .map(([cat, name]) => ({
    group: name,
    options: Object.entries(SERVICES)
      .filter(([, s]) => s.category === cat)
      .map(([id, s]) => ({ id, title: s.title })),
  }))
