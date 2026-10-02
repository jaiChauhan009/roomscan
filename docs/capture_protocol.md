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

## Measure one thing per room (video and photo tiers; LiDAR: optional check)

Video and photos get their metric scale from a depth model that guesses sizes from single
images: per room it is off by 0.4x to 3.4x and footprints come out -70 % to +137 %. No way of
filming fixes that; one number per room with a tape or laser measure does. In every room
measure the **length**: the longer side, wall to wall, at about 1 m above the floor (two
numbers, length and width, are better: their agreement is checked). The ceiling height also
works for video but is a poor reference for photos (held-out test on the sample flat: one
length per room took the photo footprint from +109 % to -23 %; the ceiling height made it worse,
+177 %), so give it only in addition to a length.

Write the numbers in metres into a text file `measurements.yaml`:

    rooms:
      01_hall:    {length: 4.20, width: 3.10, height: 2.60}   # any subset
      02_kitchen: {length: 3.55}
      any:        {height: 2.60}     # every room without its own line (optional)
    scale_reference: {length: 1.00}  # optional note, not used

* Photos: put it in the capture folder next to the room folders; room names are the folder
  names (`01_hall` or just `hall`).
* Video: put it next to the clip as `<clip>.measurements.yaml` (e.g.
  `IMG_0042.MOV.measurements.yaml`). A video has no room names: give a list, matched to the
  rooms by shape and size, largest with largest:

      rooms:
        - {length: 4.20, width: 3.10}
        - {length: 3.55, width: 2.40}

* LiDAR: the scan is never rescaled; each given number is printed next to the scanned one
  (a quick check of the scan, or of the tape).

What happens: per room, scale = median of given / fitted over the numbers given. Photos: each
room is rescaled before the rooms are joined; rooms without a number get the median of the
others. Video: one scale for the whole clip, the median over the measured rooms. The result
says the scale and where it came from (`capture.meta.known_size_*` and a warning line). A
measured quantity's interval becomes the tape's (1 cm, widened by any disagreement with the
fitted room); other quantities keep the tier's interval. Numbers that disagree with each other
by more than 10 % are reported and widen every interval.

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

## What the checker will ask you to retake

While the phone is still there, run `uv run python scripts/walkin.py E:\ --check-only`
(seconds; exit status 3 means retake). The full run prints the same lines first and goes
on. Each check says OK, WARN (usable, retake if it is easy) or RETAKE, with what to do:

| tier | you will see | because | do this |
|---|---|---|---|
| photo | RETAKE photo count | more than 12 photos in a room: taken while walking | stand in the doorway, 5-6 photos turning left to right, then one looking back |
| photo | WARN photo count | 9-12 photos, or fewer than 2 | 2-8 photos per room |
| photo | RETAKE originals | photos under 1600 px (WhatsApp sends 1280) | copy the originals (Save to Files / AirDrop), never through a chat app |
| photo | WARN originals | no focal length in the photo | copy the originals, not edited copies |
| photo | WARN sharpness | named photos are blurred | retake them holding the phone still |
| photo | WARN look-back photo | fewer than half the rooms end with a photo of the room before | step 3 of the photo tier |
| photo | WARN folders | room folders not numbered | `01_hall`, `02_kitchen`, ... |
| video | RETAKE resolution | under 720p: a chat-app copy | send the original clip |
| video | WARN / RETAKE motion | the picture moves more than 0.45 / 0.75 image widths per second (a protocol sweep: about 0.3) | slow down: one step every two seconds, no fast pans |
| video | WARN / RETAKE sharpness, blank frames | blurred frames; frames of a bare wall too close | more light, slower; keep 1.5 m from walls |
| LiDAR | RETAKE ceiling | the phone never pointed 20 deg or more above horizontal | tilt up to the ceiling once in every room (no ceiling height otherwise) |
| LiDAR | WARN tracking | the pose jumped more than 0.3 m between frames | slower; 50 cm or more from walls; do not cover the sensor |
| LiDAR | WARN turn speed | turning faster than 100 deg/s | a quarter turn in about two seconds |
| LiDAR | WARN colour video | no rgb.mp4 | copy the whole recording (Compress it first) |

The overlap between neighbouring photos is printed for information only: on bare walls the
feature matcher misses real overlap, so it never asks for a retake.

## Using the web app

The page has two parts. **Whole-home capture (optional)** at the top: choose Video or LiDAR
scan and add one file, the clip of the whole home (Video tier above: walk every room slowly,
tilt up to the ceiling once per room) or the Stray Scanner `.zip` (LiDAR tier above: Files app
> Stray Scanner > press and hold the newest folder > Compress). It takes exactly one file;
"Replace" swaps it. **Rooms** below: + Add room for each room in walk order, with its name,
the L / B / H you measured (one length or width is enough; a height alone is only compared)
and, for the photo tier, its photos. With a whole-home capture, photos are optional: a room
with only sizes is a size reference for the video or scan. Then **Check captures** (per room,
plus one block for the whole-home capture with retake advice) and **Start computing**: the
photos and the whole-home capture are computed separately, each with its own plan, and "Your
sizes vs ours" compares your sizes with each (tier column).
