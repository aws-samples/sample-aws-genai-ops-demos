#!/bin/bash
# Package three separate agent zips for S3 upload
# Each agent (discover, analyze, transform) gets its own deployment_package.zip

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/../agent" && pwd)"
SHARED_DIR="$AGENT_DIR/_shared"

agents=("discover" "analyze" "transform")

for agent in "${agents[@]}"; do
    agent_sub_dir="$AGENT_DIR/$agent"
    deployment_dir="$agent_sub_dir/deployment_package"
    output_zip="$agent_sub_dir/deployment_package.zip"

    echo ""
    echo "Packaging $agent agent from: $agent_sub_dir"

    # Clean up
    [ -d "$deployment_dir" ] && rm -rf "$deployment_dir"
    [ -f "$output_zip" ] && rm -f "$output_zip"

    # Install dependencies
    echo "  Installing dependencies..."
    uv pip install -r "$agent_sub_dir/requirements.txt" --python-platform aarch64-unknown-linux-gnu --python-version 3.13 --target "$deployment_dir"

    # Copy main.py
    cp "$agent_sub_dir/main.py" "$deployment_dir/main.py"

    # Copy shared constants
    if [ -d "$SHARED_DIR" ]; then
        mkdir -p "$deployment_dir/_shared"
        cp "$SHARED_DIR"/* "$deployment_dir/_shared/"
    fi

    # Create zip
    echo "  Creating zip archive..."
    cd "$deployment_dir"
    zip -r "$output_zip" . -q
    cd "$SCRIPT_DIR"

    # Clean up deployment_package directory
    rm -rf "$deployment_dir"

    zip_size=$(du -h "$output_zip" | cut -f1)
    echo "  $agent agent packaged: $zip_size"
done

echo ""
echo "All agents packaged successfully!"
