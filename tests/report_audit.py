import os
import json

def report_audit():
    if not os.path.exists("audit_results_milestone4.json"):
        print("Audit results file not found!")
        # Try to read the error log directly
        if os.path.exists("qa_audit_err.txt"):
            print("Reading qa_audit_err.txt...")
            with open("qa_audit_err.txt", "rb") as f:
                content = f.read().decode('utf-16le', errors='ignore')
                for line in content.splitlines():
                    if "Milestone" in line or "AUDIT COMPLETED" in line or "RESULT" in line:
                        print(line)
        return

    with open("audit_results_milestone4.json", "rb") as f:
        try:
            data = json.loads(f.read().decode('utf-16le', errors='ignore'))
            print("=== Audit Results ===")
            print(f"Final Count: {data.get('final_count')}")
            print(f"Net Growth: {data.get('net_growth_mb'):.2f} MB")
            print(f"Stability: {data.get('stability')}")
            print("\nMilestones (Count, Memory B):")
            for m in data.get('milestones', []):
                print(f" - {m[0]} faces: {m[1] / 1024 / 1024:.2f} MB")
        except Exception as e:
            print(f"Error reading JSON: {e}")

if __name__ == "__main__":
    report_audit()
