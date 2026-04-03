import sys
import os

# Test data inspired by the user's request: [(1, 'Alice'), (None, 'Bob'), (2, None)]
# We want to verify that the sort key doesn't throw TypeError.

def test_sorting_logic():
    print("--- Testing Sorting Logic ---")
    data = [
        (1, 'Alice'),
        (None, 'Bob'),
        (2, None),
        (3, ''),
        (None, None),
        (0, 'Zebra')
    ]

    print(f"Original: {data}")

    # Sorting logic from Database.get_clusters (line 375)
    # results.sort(key=lambda x: (str(x[1]) if x[1] else "", x[0] if x[0] is not None else -1))
    
    try:
        sorted_data = sorted(data, key=lambda x: (str(x[1]) if x[1] else "", x[0] if x[0] is not None else -1))
        print(f"Sorted:   {sorted_data}")
        print("SUCCESS: Sorting completed without TypeError.")
    except TypeError as e:
        print(f"FAILURE: Sorting raised TypeError: {e}")
        return False

    # Verification of order:
    # 1. Empty/None names first (str(x[1]) if x[1] else "" -> "")
    #    (2, None), (3, ''), (None, None)
    #    Sub-sort by cid: (None, None) -> -1, (2, None) -> 2, (3, '') -> 3
    # 2. 'Alice'
    # 3. 'Bob'
    # 4. 'Zebra'
    
    expected_order = [
        (None, None), # cid -1
        (2, None),    # cid 2
        (3, ''),      # cid 3
        (1, 'Alice'),
        (None, 'Bob'),
        (0, 'Zebra')
    ]
    
    if sorted_data == expected_order:
        print("SUCCESS: Sorting order matches expectation.")
    else:
        print(f"WARNING: Sorting order differs from expectation.\nExpected: {expected_order}\nActual:   {sorted_data}")

    return True

if __name__ == "__main__":
    success = test_sorting_logic()
    sys.exit(0 if success else 1)
