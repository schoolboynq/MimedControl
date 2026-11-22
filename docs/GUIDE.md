# MimeControl guide

Control the pointer, click, drag, scroll, zoom, and enter numbers with your hands in front of one or two webcams. Two cameras give MimeControl real 3D depth (see **Two cameras** below). Install and run the app on each computer you want to control. Hand tracking and input run locally. The app neither records nor uploads video.

## Install and open

1. Install [Python 3.10 or newer](https://www.python.org/downloads/) if your computer needs it. The launcher sets up a private Python environment in the extracted folder and downloads dependencies on the first run.
2. Extract all files from `MimeControl-Setup.zip` into a folder you can write to.
3. Use the launcher for your system. Use the same launcher whenever you reopen the app.

| System | Launcher |
| --- | --- |
| Windows 10 or 11, x64 | Double-click **Launch MimeControl.cmd**. Allow desktop apps to access the camera in Windows Privacy settings. **Camera Settings.cmd** opens just the camera's settings. |
| macOS 13 or later | Double-click **Launch MimeControl.command**. Allow Camera, Accessibility, and Input Monitoring for Terminal or Python in **System Settings → Privacy & Security**. Quit and reopen Terminal after changing input permissions. |
| Linux with an X11 session | Open a terminal in the extracted directory and run `python3 launch.py`. Debian and Ubuntu may need `python3-tk` and `python3-venv`. Fedora uses `python3-tkinter`. Wayland sessions are unsupported. |

To remove the app, close it and delete the extracted folder. To also erase saved reach and camera-pair calibrations, remove the **MimeControl** settings folder under `%LOCALAPPDATA%` on Windows, `~/Library/Application Support` on macOS, or your XDG configuration directory on Linux (usually `~/.config`). Developers can install from source with `python -m pip install .` inside a virtual environment and run `python -m mimecontrol`.

To update, close MimeControl, extract the new setup ZIP over the old **MimeControl** folder, and run the launcher again. It reuses the existing Python environment.

## Use it

The window walks you through three steps: **Set up**, **Calibrate**, and **Use**. On **Set up**, pick your camera by name, a control mode and a control width, then choose **Calibrate and start** the first time, or **Start** once a calibration is saved. **Skip calibration** maps the camera frame to the screen instead. The app remembers your choices, so later launches are one click. In the default **Relaxed hand** mode, extend your thumb, index and middle fingers. Let your ring and pinky fingers bend loosely toward your palm. Move your whole hand to aim. The white ring in the camera preview marks the center of your four knuckles.

The app asks the camera for the smallest video mode that keeps its full field of view and the detail tracking needs, for example 640 × 360 instead of 3840 × 2160 on a 4K webcam. Converting full-size 4K frames can take more time than tracking itself and make the video stutter. To change the mode on Windows, the app takes control of the camera; if another app has control, the app shares the camera at its current mode instead. Cameras that need control every time open with it straight away, which saves about a third of a second, and a second camera starts alongside the first. The app also requests the widest zoom setting unless you chose another in **Camera settings**. Choose **Check camera** to see the view.

**Camera filter** defaults to **Auto**, which adjusts to the lighting. If the tracker still loses your hand, choose **Low light** for a dim room, **Backlit** when a bright window is behind your hand, or **Low contrast** when your hand blends into a washed-out picture. Choose **Off** to see the original image. You can switch filters while testing the camera, calibrating, or tracking. The tracker uses the filtered image even with the preview closed.

Hand tracking works with sideways and upside-down hands, and it uses 3D joint estimates for gestures when your hand tilts. When you sit farther back, the tracker automatically searches smaller regions of the frame and uses up to 640 × 480 camera pixels to track your fingertips. The preview still shows the full camera view. Your webcam's resolution and lighting affect how far away it can distinguish individual fingers.

| Action | Gesture |
| --- | --- |
| Move the pointer | In **Relaxed hand** or **Palm + pinch**, place your knuckles at the desired spot in the camera view with your fingers comfortably bent. In **Pinch focus**, form a loose C with thumb and index to aim from their midpoint. In **Point + pinch** and **Air tap**, aim with your index fingertip. |
| Switch screens | Reach past the side of your box toward the other screen and hold for a moment, or flick your pointing hand sharply toward it. See **Using two screens**. |
| Window commands | Make a fist, pause, then swing it left, right, up or down, or push it toward the screen or pull it back. See **Fist gestures**. |
| Left-click | In **Relaxed hand**, bring your index and middle fingertips together toward your thumb, then open them. Return to the navigation pose for the next click. In the three older pinch modes, touch your thumb and index fingertips together, then reopen. The mouse button goes down when your fingers meet and comes up when they part, like a real mouse button. In **Air tap**, quickly move your pointing hand toward the webcam, then pull it back. |
| Hold and drag | Close the click gesture and keep it closed: the button stays down. Move your hand to drag, for example to select text, move a file or a window, or pull a slider, then open your fingers to let go. The pointer stays put until your hand has moved a little, so a quick click never turns into a small drag. If your hand leaves the view for more than a moment, the app lets go. Not available in **Air tap**. |
| Right-click | In **Relaxed hand**, squeeze your middle, ring and pinky fingers into your palm with your index still extended. Return to navigation to rearm. In the other modes, bring your middle fingertip near your thumb, touch them together, then reopen. Keep your index finger clear throughout. |
| Scroll | Extend your index and middle fingers. In **Relaxed hand**, tuck your thumb across your palm. Move your hand up or down. |
| Zoom | Extend your index, middle, and ring fingers. In **Relaxed hand**, tuck your thumb across your palm. Move up to zoom in or down to zoom out. The app sends Ctrl+wheel on Windows and Linux, and Command+`=` or Command+`-` on macOS. |
| Number pad | Spread your fingers and hold your palm still for about a second (you can reassign this under **Gestures…**), or press **Ctrl+Alt+N**. Focus the app you want to type into, aim to highlight a key, and use your selected click gesture to enter it. Repeat the palm gesture or shortcut to close the pad. |
| Pause | Press **Ctrl+Alt+Space** while tracking, choose **Pause**, or press **Esc** in the MimeControl window. Pausing releases the camera. |

For browser games, keep the game focused and use the pointer and your selected click gesture. Each active camera runs hand inference with one CPU thread. The app requests up to 30 frames per second, and turns off the preview by default. Response depends on the webcam, lighting, and computer speed.

### Calibrate: draw your control box

Choose **Calibrate and start** on the setup page, **Calibrate → Calibrate from scratch** while tracking, or press **Ctrl+Alt+R**. Sit where you normally use the computer. The camera doesn't need to face you squarely: a tilted lid, a camera off to one side, or a slightly rotated webcam all work. Calibration uses both hands. Desktop gestures pause while you calibrate. **Start over** restarts, and **Cancel** keeps your previous calibration.

A **Calibration steps** panel pops up beside the window, in large text you can read sitting back. It shows the step you're on, what to do, live feedback with a bar that fills as you hold still, and the next three steps with a small picture of each move. Every part keeps its size, so neither the panel nor the drawing view moves as the words change. Close it if you like and bring it back with **Show steps**. It stays where you put it.

You draw a box in the air with both hands, one side at a time, starting with the side furthest from you. Each side is a mirrored outline:

1. Put your hands together at the top center of the box and hold until the rings fill.
2. Sweep them apart to the top corners and hold.
3. Bring them straight down to the bottom corners and hold.
4. Bring them together at the bottom center and hold.

Draw the **Furthest out** side with your arms reached out toward the screen as far as is comfortable. Then draw the **Close in** side with your hands pulled back toward you, and finally the **Middle**, halfway between. The drawing view shows the box filling in, a trail behind each hand, and a gauge of how far out each hand is. The map is refined after every side. It takes about half a minute.

With a second camera, calibration starts with **Turn both hands**. Hold up both hands with your fingers spread, then slowly turn them and move them around a little as you bring them in close and back out. When the cameras line up, you draw the box the same way, now measured in the 3D space both cameras share. If they need more motion, the app asks you to keep turning a little longer. If they can't line up, it carries on with one camera and says so.

The app saves the box and uses it straight away. Calibrate again after moving the computer or either camera. **Use calibration** under **More settings** switches between your box and regular frame mapping.

### Refine or redo a side

Once you have a box, open **Calibrate** on the use page, or **Refine or redo a side…** on the setup page:

- **Refine: draw the box again** blends a new drawing into your saved box instead of replacing it. Each drawing averages out a little more hand wobble, so the box usually gets steadier over the first few passes, most of all with a camera facing you. Newer drawings count more, and the app keeps up to eight. Refining keeps cameras that are already lined up, so it skips the turning step.
- **Redo one side** redraws just **Furthest out**, **Close in** or **Middle** and replaces that side, keeping the rest.
- **Calibrate from scratch** starts over. Do this after moving the computer, either camera or your seat.

Afterwards, the steps panel shows how far the corners moved as a share of the screen, and which corners moved most. A big change after refining usually means something moved, so calibrate from scratch instead.

### Control width

**Control width** on the setup page, also on the use page, sets how much of the box you drew spans the screen: **Smaller** (75%), **As drawn**, or **Larger** (125%). Smaller needs less arm movement; larger gives finer control. It changes straight away, with no need to calibrate again.

#### How it works

The app finds each hand's position in 3D from where it is in the picture and how big it looks, or from both views with a second camera. With two hands in view, it works out which hand is which in each camera, so a hand is never matched with its neighbor at the wrong distance. It fits one map through everything you drew, then makes each side's corners land exactly on the screen's corners. A camera turned or tilted relative to you only changes the map, not your box. The three sides can differ in size and shape. With a second camera, turning your hand doesn't change its measured distance, and the app bridges any frames where the second view misses it. Older saved calibrations still load.

### Two cameras

One camera works, but it has to guess how far away your hand is from how big it looks. A second webcam lets MimeControl measure depth from both views, the way your two eyes do, so the box you draw and your push and pull gestures track more reliably, and turning your hand doesn't change its measured distance. Any two webcams work, for example a USB webcam on top of the monitor and a laptop's built-in camera. Mount them a hand-width or more apart and aim both at the space where your hands will be. No measuring is needed.

Pick it under **Second camera** on the setup page, then choose **Calibrate and start**. Turning both hands lets the app find the timing difference between the cameras and how they're placed relative to each other. It uses both hands' views and estimates the cameras' spacing from typical adult palm size. Landmark samples stay in memory and never leave the computer.

If you calibrate again without moving the cameras, the app averages the new alignment into the saved one, so it improves over several runs. If a camera moved, it starts a fresh alignment automatically. Set **Second camera** to **None** to go back to one camera. The app remembers your choice.

### Using two screens

You control one screen at a time. The top of the window shows which one, like **2 displays · using 1**, numbered left to right. To move control to another screen:

- **Reach past the side of your box** toward that screen and hold for a moment. The status line says *Keep reaching…* while it waits. After switching, bring your hand back inside the box before reaching that way again.
- Or **flick** your pointing hand sharply toward it after a brief pause.

Either way, the pointer then follows your hand on the new screen. Both can be turned off under **Gestures…**. The number pad opens on the screen you're using.

On Windows, if both screens show the same image and you want to control them separately, press **Win + P** and choose **Extend**. In **Settings → System → Display**, use **Identify** and arrange the screen rectangles to match your desk, so left and right match where the screens really are.

### Fist gestures for windows and more

A closed fist never moves the pointer or clicks in any control mode, so whole-arm fist movements are free for commands. Make a fist, pause for a moment, then:

| Fist gesture | Does by default (Windows) |
| --- | --- |
| Swing left / right | Snap the window left / right |
| Swing up / down | Maximize / minimize the window |
| Push toward the screen | Task view |
| Pull back toward you | Show desktop |

Swings are about 30 cm in under half a second; pushes and pulls are about a quarter of the distance to the camera. Bringing your arm back after a swing doesn't count as the opposite swing. Pause first if you want the opposite command. On macOS, swinging left and right moves between desktops instead.

### Customize gestures

Choose **Gestures…** on the use page, or **Customize gestures…** on the setup page, to pick what each gesture does: every fist swing, push and pull, the spread palm held still, reaching past the side of your box, and the flick. Actions include snapping, maximizing, minimizing and closing windows, moving a window to the other screen, task view, show desktop, switching apps, back and forward, virtual desktops, media and volume keys, the number pad, pausing tracking, switching screens, or **Custom shortcut…**, where you type any shortcut such as **Ctrl+Shift+T**. **Use fist gestures** turns all fist gestures on or off, and **Reset to defaults** restores the table above. Your choices are saved and the guide on the use page follows them.

### Sticky targets and clicking

On Windows, **Sticky targets** starts enabled. When you move slowly over something you can act on in the active window, such as a button, link, check box, tab, slider, menu item, editable text box, or a list item you can select, the pointer slows down. It settles on the control, and small tremors don't knock it off. It never jumps. Quick moves cross controls at full speed, and wide controls such as long text fields and menus slow the pointer only across their narrow side. Once the pointer is off a control, it gradually catches up with your hand. At the edge of your reach it always reaches the edge of the screen. Plain text, read-only fields, ordinary bulleted text and decorative parts of a page don't slow it, so it glides over regular text. Sticky targets use what apps and browsers report through Windows accessibility. Games that draw everything on a canvas usually provide free aiming only. Turn off **Sticky targets** under **More settings** whenever you want full manual aim.

**Hold to drag** under **More settings** starts enabled. Turn it off to make each left click a quick press and release the moment your fingers meet, as in earlier versions.

In the older pinch modes, separate your thumb and index, then squeeze and reopen. A quick pinch can register on its first contact frame when the camera sees the fingertips moving together. The pointer holds its position during the click. On Windows, **Show clicks** briefly draws a cyan ring at the cursor for a left click and an orange double ring for a right click. Right-clicks, number pad keys and Air taps hold the mouse button down for about 60 milliseconds so games that check once per frame can see them.

### Air tap

Choose **Air tap** under **Control mode**. Aim with your index finger, make a short, quick push toward the webcam, then pull back to click again. The cursor holds its position during the tap. Air taps work on the desktop and number pad. To drag, choose **Relaxed hand** or a pinch mode. When using two hands, lower or relax your other hand to leave scroll or zoom mode before tapping.

### Pointer response

**Relaxed hand** adds stronger filtering around a stationary aim and holds the last target as you form either click. **Balanced** smooths small motion and follows larger movements quickly. In **Palm + pinch** and **Pinch focus**, the desktop cursor responds quickly when you start and stop moving, with a little drag during sustained motion. Choose **Steady** for finer aiming or **Fast** for quicker movement. With regular frame mapping, holding your hand at the same spot in the frame gives the same aim at different distances. The app uses estimated fingertip depth to recognize pinch clicks.

In **Relaxed hand** and **Palm + pinch**, **Palm aim area** (under **More settings**, when calibration is off) defaults to **Full frame**. If a screen edge is hard to reach while keeping your hand visible, choose **Large**, **Medium**, or **Small**. The rectangle in the camera preview shows the part of the picture that maps to the whole display. With calibration on, the preview outlines the area that reaches the screen corners at your hand's current distance. A smaller rectangle takes less reach. When the number pad opens, its own aiming rectangle appears in the preview.

### Two-hand controls

Enable **Two-hand controls** and show your pointer hand first. Use your selected aim and click gestures with that hand while your other hand is relaxed or out of frame.

| Action | Gesture |
| --- | --- |
| Scroll | Use your normal navigation pose with the aiming hand. Raise your other hand with its palm open, then move that hand up or down. |
| Zoom | Hold both palms open. Spread them apart to zoom in or move them together to zoom out. |

Show one open palm on its own or use **Ctrl+Alt+N** to toggle the number pad. Two-hand mode runs the hand model more often. Leave it off for the lightest tracking workload.

### Camera settings

On Windows, choose **Camera settings…** on the setup page or **Camera…** while tracking, or double-click **Camera Settings.cmd** to open only this window. It shows a live view and the settings the camera itself offers. These usually include brightness, contrast, saturation, sharpness, hue, white balance, exposure, focus, zoom and anti-flicker, each with the range and **Auto** options the camera reports. With two cameras, pick either one from the list.

Changes go straight to the camera, the way the maker's app sends them, so they stay for every app. MimeControl also remembers what you change and applies it again whenever it opens that camera. **Reset to camera defaults** returns everything to the camera's own defaults and forgets your changes. The widest zoom suits gestures. If your hands look soft in the view, turn off focus **Auto** and move **Focus** until they're sharp. If another app has control of the camera, close that app to change settings here.

No driver is needed: most webcams use Windows' built-in USB video driver, and video never passes through the maker's app. Keep the maker's app only for firmware updates and brand-specific extras such as HDR or noise reduction, which use private commands.

## Camera troubleshooting

Choose **Check camera** on the setup page to see the live preview before turning on gestures. On Windows, the test tries Windows MediaCapture first and verifies that multiple usable frames arrive. If the stream stalls, it tries Media Foundation and DirectShow. The status shows the camera or driver that delivers video. If the preview works, choose **Start** (or **Calibrate and start**) straight from the check. Use **Copy camera error** to copy the diagnostic details if setup fails. If something else goes wrong, the app stops tracking, shows **Copy error details**, and adds the report to **errors.log** in the MimeControl settings folder.

1. On Windows, check whether the built-in **Camera** app shows video. Then close it and any browser tabs or meeting apps using the camera.
2. Open **Settings → Privacy & security → Camera** on Windows 11 or **Settings → Privacy → Camera** on Windows 10. Enable **Camera access** and **Let desktop apps access your camera**.
3. Choose **Check camera** again. If the camera fails or shows the wrong feed, pick another one from the **Camera** list. If the preview is black, check the camera's shutter or privacy key.
4. If a 4K or other high-resolution webcam keeps disconnecting, plug it straight into a USB 3 port rather than a hub, and close other apps that use it so MimeControl can switch it to a lighter video mode.

## Model credits

The bundled [palm detector](https://github.com/opencv/opencv_zoo/tree/main/models/palm_detection_mediapipe) and [hand pose model](https://github.com/opencv/opencv_zoo/tree/main/models/handpose_estimation_mediapipe) are Apache-2.0 MediaPipe models converted to ONNX by OpenCV Zoo. The model license is included with the installed package.
