#!/usr/bin/env python3
"""CDK app for AWS Feature Relevance Notification System."""
import aws_cdk as cdk

from shared.utils.aws_utils import get_region

from lib.feature_relevance_stack import FeatureRelevanceStack

app = cdk.App()

region = get_region()

FeatureRelevanceStack(
    app,
    f"FeatureRelevanceNotification-{region}",
    description="AWS Feature Relevance Notification System: AI-powered scoring of AWS announcements against workload profiles (uksb-do9bhieqqh)(tag:feature-relevance-notification,operations-automation)",
    env=cdk.Environment(region=region),
)

app.synth()
