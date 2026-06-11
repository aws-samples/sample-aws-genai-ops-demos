import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { DemoScenarioAccountHealthStack } from '../lib/demo-scenario-account-health-stack';

describe('DemoScenarioAccountHealthStack', () => {
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App();
    const stack = new DemoScenarioAccountHealthStack(app, 'TestStack', {
      env: { account: '123456789012', region: 'us-east-1' },
    });
    template = Template.fromStack(stack);
  });

  // -------------------------------------------------------------------------
  // Requirement 4.1: VPC with correct CIDR and DNS settings
  // -------------------------------------------------------------------------
  test('creates VPC with CIDR 10.99.0.0/16 and DNS settings enabled', () => {
    template.hasResourceProperties('AWS::EC2::VPC', {
      CidrBlock: '10.99.0.0/16',
      EnableDnsSupport: true,
      EnableDnsHostnames: true,
    });
  });

  // -------------------------------------------------------------------------
  // Requirement 4.2: Two subnets with correct CIDRs
  // -------------------------------------------------------------------------
  test('creates subnet with CIDR 10.99.1.0/24', () => {
    template.hasResourceProperties('AWS::EC2::Subnet', {
      CidrBlock: '10.99.1.0/24',
    });
  });

  test('creates subnet with CIDR 10.99.2.0/24', () => {
    template.hasResourceProperties('AWS::EC2::Subnet', {
      CidrBlock: '10.99.2.0/24',
    });
  });

  test('creates exactly two subnets', () => {
    const subnets = template.findResources('AWS::EC2::Subnet');
    expect(Object.keys(subnets)).toHaveLength(2);
  });

  // -------------------------------------------------------------------------
  // Requirement 4.3: DB Subnet Group exists
  // -------------------------------------------------------------------------
  test('creates DB subnet group', () => {
    template.hasResourceProperties('AWS::RDS::DBSubnetGroup', {
      DBSubnetGroupName: 'goat-demo-db-subnet-group',
      DBSubnetGroupDescription: Match.stringLikeRegexp('.*'),
    });
  });

  // -------------------------------------------------------------------------
  // Requirement 4.4: Two EC2 instances with t3.micro instance type
  // -------------------------------------------------------------------------
  test('creates two EC2 instances of type t3.micro', () => {
    const instances = template.findResources('AWS::EC2::Instance', {
      Properties: {
        InstanceType: 't3.micro',
      },
    });
    expect(Object.keys(instances)).toHaveLength(2);
  });

  // -------------------------------------------------------------------------
  // Requirement 4.5: RDS instance with correct configuration
  // -------------------------------------------------------------------------
  test('creates RDS instance with db.t3.micro class, mysql engine, and 20GB storage', () => {
    template.hasResourceProperties('AWS::RDS::DBInstance', {
      DBInstanceClass: 'db.t3.micro',
      Engine: 'mysql',
      AllocatedStorage: '20',
      StorageType: 'gp2',
    });
  });

  // -------------------------------------------------------------------------
  // Requirement 4.6: Unattached EBS volume (10GB gp2)
  // -------------------------------------------------------------------------
  test('creates EBS volume with 10GB gp2', () => {
    template.hasResourceProperties('AWS::EC2::Volume', {
      Size: 10,
      VolumeType: 'gp2',
    });
  });

  // -------------------------------------------------------------------------
  // Requirement 4.7: Elastic IP exists
  // -------------------------------------------------------------------------
  test('creates an Elastic IP', () => {
    template.resourceCountIs('AWS::EC2::EIP', 1);
  });

  // -------------------------------------------------------------------------
  // Requirement 3.1, 3.2: All resources have correct tags
  // -------------------------------------------------------------------------
  describe('resource tagging', () => {
    test('VPC has goat-demo=true and goat-scenario=a tags', () => {
      template.hasResourceProperties('AWS::EC2::VPC', {
        Tags: Match.arrayWith([
          Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
          Match.objectLike({ Key: 'goat-scenario', Value: 'a' }),
        ]),
      });
    });

    test('subnets have goat-demo=true and goat-scenario=a tags', () => {
      template.hasResourceProperties('AWS::EC2::Subnet', {
        CidrBlock: '10.99.1.0/24',
        Tags: Match.arrayWith([
          Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
          Match.objectLike({ Key: 'goat-scenario', Value: 'a' }),
        ]),
      });
      template.hasResourceProperties('AWS::EC2::Subnet', {
        CidrBlock: '10.99.2.0/24',
        Tags: Match.arrayWith([
          Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
          Match.objectLike({ Key: 'goat-scenario', Value: 'a' }),
        ]),
      });
    });

    test('EC2 instances have goat-demo=true and goat-scenario=a tags', () => {
      const instances = template.findResources('AWS::EC2::Instance');
      for (const [, resource] of Object.entries(instances)) {
        const tags = (resource as any).Properties.Tags;
        expect(tags).toEqual(
          expect.arrayContaining([
            expect.objectContaining({ Key: 'goat-demo', Value: 'true' }),
            expect.objectContaining({ Key: 'goat-scenario', Value: 'a' }),
          ])
        );
      }
    });

    test('RDS instance has goat-demo=true and goat-scenario=a tags', () => {
      template.hasResourceProperties('AWS::RDS::DBInstance', {
        Tags: Match.arrayWith([
          Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
          Match.objectLike({ Key: 'goat-scenario', Value: 'a' }),
        ]),
      });
    });

    test('EBS volume has goat-demo=true and goat-scenario=a tags', () => {
      template.hasResourceProperties('AWS::EC2::Volume', {
        Tags: Match.arrayWith([
          Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
          Match.objectLike({ Key: 'goat-scenario', Value: 'a' }),
        ]),
      });
    });

    test('Elastic IP has goat-demo=true and goat-scenario=a tags', () => {
      template.hasResourceProperties('AWS::EC2::EIP', {
        Tags: Match.arrayWith([
          Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
          Match.objectLike({ Key: 'goat-scenario', Value: 'a' }),
        ]),
      });
    });

    test('DB subnet group has goat-demo=true and goat-scenario=a tags', () => {
      template.hasResourceProperties('AWS::RDS::DBSubnetGroup', {
        Tags: Match.arrayWith([
          Match.objectLike({ Key: 'goat-demo', Value: 'true' }),
          Match.objectLike({ Key: 'goat-scenario', Value: 'a' }),
        ]),
      });
    });
  });

  // -------------------------------------------------------------------------
  // Requirement 4.8: Stack outputs are defined
  // -------------------------------------------------------------------------
  describe('stack outputs', () => {
    test('exports VPC ID', () => {
      template.hasOutput('VpcId', {
        Description: Match.stringLikeRegexp('.*VPC.*'),
      });
    });

    test('exports Subnet 1 ID', () => {
      template.hasOutput('Subnet1Id', {
        Description: Match.stringLikeRegexp('.*Subnet 1.*'),
      });
    });

    test('exports Subnet 2 ID', () => {
      template.hasOutput('Subnet2Id', {
        Description: Match.stringLikeRegexp('.*Subnet 2.*'),
      });
    });

    test('exports Instance 1 ID', () => {
      template.hasOutput('Instance1Id', {
        Description: Match.stringLikeRegexp('.*Instance 1.*'),
      });
    });

    test('exports Instance 2 ID', () => {
      template.hasOutput('Instance2Id', {
        Description: Match.stringLikeRegexp('.*Instance 2.*'),
      });
    });

    test('exports RDS instance ID', () => {
      template.hasOutput('RdsInstanceId', {
        Description: Match.stringLikeRegexp('.*RDS.*'),
      });
    });

    test('exports EBS volume ID', () => {
      template.hasOutput('EbsVolumeId', {
        Description: Match.stringLikeRegexp('.*EBS.*'),
      });
    });

    test('exports Elastic IP allocation ID', () => {
      template.hasOutput('EipAllocationId', {
        Description: Match.stringLikeRegexp('.*Elastic IP.*'),
      });
    });
  });
});
