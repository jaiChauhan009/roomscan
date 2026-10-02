# Capture protocol (Route 2: stock capture tools)

One page. Follow it literally. Pick one tier, then do: **Before you start**, that tier's
section, **Hand-off**. You need the phone and an empty USB-C flash drive (exFAT).

## Before you start (all tiers)

1. Phone: any iPhone 15 or newer for video and photos. LiDAR needs a Pro model
   (iPhone 15 Pro / Pro Max or a newer Pro) or an iPad Pro.
2. Open every interior door fully (a closed door is measured as wall). Switch on all
   lights. Open the blinds in daytime.
3. Wipe the camera lenses with a soft cloth.
4. Video and photo tiers: Settings > Camera > Formats > **Most Compatible**. In the Camera
   app the zoom button must read **1x** (tap it until it does). Never zoom.
5. Hold the phone upright (portrait) at chest height, the same height all the time.

## LiDAR tier: Stray Scanner (30-40 seconds per room)

Install **Stray Scanner** (free, App Store). Open it once and allow camera access.
One recording for the whole property:
1. Stand in the room you enter the property by (usually the hall), 1 m from a wall. Tap
   the red record button.
2. Walk slowly (one step per second) once around the room, pointing the phone at the walls
   from 1 to 2.5 m away, until every wall, corner, door frame and window has been in view.
3. Tilt the phone up once to sweep the ceiling, then down once to sweep the floor along
   the walls.
4. Walk slowly through a doorway into the next room, pointing at the door frame as you
   pass. Repeat 2-4 until every room is done.
5. Walk back to where you started, point at the wall you began with, tap stop.

Avoid: fast turns; covering the lens or the LiDAR sensor (the black dot next to the
lenses); getting closer than 50 cm to a wall; pointing at a mirror or window for more than
a moment.

**Onto the drive:** plug the drive into the phone. Files app > Browse > On My iPhone >
Stray Scanner. Press and hold the newest folder (several? tap ⋯ at the top, choose Date:
the newest is first) > Compress. Press and hold the new .zip > Share > Save to Files > the
drive > Save.

## Video tier: Camera app (about one minute per room)

Camera app, mode VIDEO (not Cinematic, not Slo-mo), 1x. One clip for the whole property,
the same walk as the LiDAR tier with three differences:
1. Walk at half speed: one step every two seconds.
2. Never turn on the spot: step sideways while you turn, so the phone keeps moving.
3. Keep some floor in the bottom of the picture all the time.

Avoid: pointing at a blank wall from closer than 1.5 m, fast pans, walking backwards.

**Onto the drive:** Photos app > open the clip > Share > Save to Files > the drive > Save.

## Photo tier: Camera app (2 to 8 photos per room)

Camera app, mode PHOTO, 1x, flash off, Live Photo off. Rooms in the order you walk
through the property, starting with the room you enter it by. In each room:
1. Stand in the doorway you entered the room through, your back against the door frame.
2. Take 3 to 6 photos sweeping from the far left of the room to the far right, turning a
   little between photos so each overlaps the previous one by about a third. Floor and
   ceiling must both be in every photo; step back or tilt if not.
3. Every room except the first: without moving your feet, turn around and take **one last
   photo** of the room you came from.

Avoid: changing the phone's height between photos, zooming, taking a room's photos from
more than one spot.

**Onto the drive, one folder per room:** Photos app > Select > tap the photos of the first
room > Share > Save to Files > the drive > new-folder icon > name it `01_<room>` (e.g.
`01_hall`) > Save. Repeat for each room in walk order: `02_kitchen`, `03_bedroom`, ...

## Hand-off (all tiers)

The drive must hold this one capture only. Plug it into the computer and, in the roomscan
folder, run (E: is the drive letter File Explorer shows; on a Mac use /Volumes/<drive>):

    uv run python scripts/walkin.py E:\

It finds the capture on the drive (Stray Scanner .zip or folder, video clip, or room
folders), runs the whole pipeline cold, prints the time of every stage and, per room: floor
area, every wall length with its 90 % interval and the side of the plan it is on, ceiling
height, openings with widths. A wrong or incomplete hand-off stops with a one-line message
saying what was found. On a laptop CPU a LiDAR or photo capture takes a few minutes, a
video about ten minutes for a two-minute clip. Outputs (`result.json`, `plan.png`,
`plan.svg`, `walkin.txt`) go to `runs/walkin_<capture>_<date>_<time>/`.
`uv run roomscan run <capture>` runs the same pipeline without the table (it needs the
recording unzipped).
