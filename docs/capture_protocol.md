# Capture protocol (Route 2: stock capture tools)

One page. Follow it literally. No engineering knowledge needed.

## Before any capture (all tiers)

1. Open every interior door fully. Closed doors are measured as wall.
2. Switch on all lights. Open blinds if it is daytime.
3. Clean the camera lenses with a soft cloth.
4. Hold the phone upright (portrait), at chest height, the same height for the whole capture.

## Tier 1: LiDAR (iPhone 15 Pro / Pro Max or newer Pro, iPad Pro)

**Install:** "Stray Scanner" from the App Store (free). Open it and allow camera access.

**Capture, one recording for the whole property:**
1. Stand in the first room, 1 m from a wall. Tap the red record button.
2. Walk slowly (one step per second) around each room, keeping the phone pointed at the
   walls from 1-2.5 m away. Go once around the room so every wall, every corner and every
   door and window frame has been in view.
3. In each room, tilt the phone up once to sweep the ceiling, then down once to sweep the
   floor along the walls.
4. Walk through each doorway slowly, pointing the phone at the door frame as you pass.
5. Finish by walking back to where you started and pointing at the same wall you began with.
6. Tap stop. Allow about 30-40 seconds per room.

**Avoid:** fast turns, covering the lens or the LiDAR sensor (black dot next to the
lenses), walking closer than 50 cm to a wall, pointing at a mirror or window for more than
a moment.

**Hand over the files:** Files app → On My iPhone → Stray Scanner → press and hold the
newest folder → Compress → share the .zip (AirDrop, cable or cloud drive). Unzip it on the
computer and run:

    roomscan run <unzipped folder>

## Tier 2: Video (any iPhone 15 or newer)

**Tool:** the built-in Camera app. Mode VIDEO, lens 1x, default resolution. Do not zoom.

**Capture, one clip for the whole property:** same walk as the LiDAR tier, with three
differences:
1. Walk at half speed: one step every two seconds.
2. Never turn while standing on one spot. Always step sideways as you turn, so the phone
   keeps moving.
3. Keep some floor in the bottom of the picture at all times.

Allow about one minute per room. **Avoid:** pointing at a blank wall from closer than 1.5 m,
fast pans, walking backwards.

**Hand over:** share the clip (AirDrop, cable or cloud drive; keep the original, do not
send through a chat app that re-compresses it) and run:

    roomscan run <clip.mov>

## Tier 3: Photos (any iPhone 15 or newer)

**Tool:** the built-in Camera app. Mode PHOTO, lens 1x, flash off, Live Photo off. Hold the
phone upright (portrait) at chest height. 2 to 8 photos per room.

**For each room, in the order you walk through the property:**
1. Stand in the doorway you entered the room through, your back against the door frame.
2. Take 3 to 6 photos sweeping from the far left of the room to the far right. Turn a
   little between photos so that each photo overlaps the previous one by about a third.
   Floor and ceiling should both be visible in each photo; step back or tilt if not.
3. Every room except the first: without moving your feet, turn around and take **one last
   photo** of the room you came from.

**Avoid:** changing the height of the phone between photos, zooming, taking a room's photos
from more than one spot.

**Hand over:** on the computer make one folder per room, named in walk order
(`01_hall`, `02_kitchen`, `03_bedroom`, ...), and put each room's photos in its folder.
Keep the original file names so the photos stay in the order they were taken. Put all room
folders in one folder and run:

    roomscan run <folder with the room folders>

## What you get

`result.json` (all measurements with 90 % intervals, damage, flags, scope) and `plan.png`
(the whole-property floor plan) in `runs/<capture name>/`.
