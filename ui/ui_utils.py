from typing import Any, Union

from core.models import FaceDisplayItem, LibraryViewHeader, LibraryViewItem


def get_item_grouping_keys(item: Union[LibraryViewItem, FaceDisplayItem]) -> tuple[str, str]:
    """
    Extracts (date_str, location_label) for grouping from a display item.
    """
    if isinstance(item, LibraryViewItem):
        m = item.media
        cap_date = m.capture_date or ""
        city = m.city or ""
        pref = m.prefecture or ""
        country = m.country or ""
    else:
        # FaceDisplayItem
        f = item.face
        cap_date = f.capture_date or ""
        meta = f.metadata or {}
        city = meta.get("city", "")
        pref = meta.get("prefecture", "")
        country = meta.get("country", "")

    date_str = cap_date.split(" ")[0] if cap_date else "Unknown Date"

    if country in ["Japan", "日本", "JP"]:
        loc_label = f"{pref}, {city}" if pref and city else (pref or city)
    else:
        loc_label = f"{country}, {city}" if country and city else (country or city)

    if not loc_label or loc_label.strip() == ",":
        loc_label = "Unknown Location"

    return date_str, loc_label


def group_media_by_date_and_location(
    media_list: list[Union[LibraryViewItem, FaceDisplayItem]], current_last_key: Any = None
) -> tuple[list[Union[LibraryViewItem, FaceDisplayItem, LibraryViewHeader]], Any]:
    """
    Groups a list of display items by date and location, inserting LibraryViewHeader objects.
    """
    if not media_list:
        return [], current_last_key

    grouped = []
    current_key = current_last_key  # (date, location)

    for item in media_list:
        date_str, loc_label = get_item_grouping_keys(item)
        item_key = (date_str, loc_label)

        if item_key != current_key:
            grouped.append(
                LibraryViewHeader(is_header=True, date_header=date_str, location_header=loc_label)
            )
            current_key = item_key

        grouped.append(item)

    return grouped, current_key
