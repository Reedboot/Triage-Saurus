#!/usr/bin/env python3
import argparse
import yaml
import sys

def summarize_rule(rule_file):
    """
    Parses a YAML rule file and prints a summary.
    """
    try:
        with open(rule_file, 'r') as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: File not found at {rule_file}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file: {e}", file=sys.stderr)
        sys.exit(1)

    if 'rules' in data and data['rules']:
        for rule in data['rules']:
            print(f"ID: {rule.get('id', 'N/A')}")
            print(f"Severity: {rule.get('severity', 'N/A')}")
            print(f"Message: {rule.get('message', 'N/A').strip()}")

            metadata = rule.get('metadata', {})
            if metadata:
                print("\nMetadata:")
                for key, value in metadata.items():
                    print(f"  {key.capitalize()}: {value}")
            print("-" * 20)
    else:
        print("No rules found in the file.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Summarize a Triage-Saurus rule file.")
    parser.add_argument("rule_file", help="Path to the rule file to summarize.")
    args = parser.parse_args()
    summarize_rule(args.rule_file)
