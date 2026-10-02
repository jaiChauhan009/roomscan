"""A room photographed while walking is thinned to the protocol's size, look-back kept."""
from roomscan.frontends.photos import MAX_PER_ROOM, thin_room


def test_many_photos_are_thinned_evenly_keeping_the_look_back():
    files = [f"IMG_{i:03d}.jpg" for i in range(55)]
    kept = thin_room(files)
    assert len(kept) == MAX_PER_ROOM
    assert kept[0] == files[0] and kept[-1] == files[-1]  # the sweep's start, the look-back photo
    gaps = [files.index(b) - files.index(a) for a, b in zip(kept, kept[1:-1])]
    assert max(gaps) - min(gaps) <= 1  # evenly spaced
    assert kept == sorted(kept)  # walk order kept


def test_a_room_within_the_protocol_is_untouched():
    files = [f"IMG_{i}.jpg" for i in range(6)]
    assert thin_room(files) == files
