def test_logic():
    # Mock media from DB
    media = [
        {
            "file_path": "a.jpg",
            "group_hash": "hash1",
            "is_duplicate": True,
            "metadata": {"country": "Japan", "prefecture": "Tokyo"},
        },
        {
            "file_path": "b.jpg",
            "group_hash": "hash1",
            "is_duplicate": True,
            "metadata": {"country": "Japan", "prefecture": "Tokyo"},
        },
        {
            "file_path": "c.jpg",
            "group_hash": "hash2",
            "is_duplicate": True,
            "metadata": {"country": "USA", "prefecture": ""},
        },
        {
            "file_path": "d.jpg",
            "group_hash": "hash2",
            "is_duplicate": True,
            "metadata": {"country": "USA", "prefecture": ""},
        },
    ]

    # Simulation state
    last_hash = None
    hash_to_id = {}
    next_group_id = 1
    display_data = []

    is_dupe_view = True  # Simulating Duplicates View

    for item in media:
        current_h = item.get("group_hash")
        is_duplicate = item.get("is_duplicate", False)

        if is_duplicate and current_h:
            if current_h not in hash_to_id:
                hash_to_id[current_h] = next_group_id
                next_group_id += 1
            item["group_id"] = hash_to_id[current_h]

        if is_dupe_view:
            if current_h and current_h != last_hash:
                display_data.append(
                    {"is_header": True, "group_id": item.get("group_id"), "group_hash": current_h}
                )
                last_hash = current_h

        display_data.append(item)

    # Print results
    for d in display_data:
        if d.get("is_header"):
            print(f"HEADER: Group #{d['group_id']} (Hash: {d['group_hash']})")
        else:
            print(f"  ITEM: {d['file_path']} -> Group #{d.get('group_id')}")


if __name__ == "__main__":
    test_logic()
