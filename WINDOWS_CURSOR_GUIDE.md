# Windows Red Cursor Setup

RedShift can open the Windows pointer accessibility page from the tray menu or the controls window:

```powershell
start ms-settings:easeofaccess-mousepointer
```

Microsoft does not provide a stable registry value for the custom accessibility pointer color. Windows Settings generates custom cursor files for the color you choose, so the safest setup is to use the built-in Accessibility page.

## Set the Cursor to Red

1. Open RedShift.
2. Choose **Cursor Setup** in the controls window or **Open Cursor Settings** in the tray menu.
3. In Windows Settings, go to **Accessibility > Mouse pointer and touch**.
4. Under **Mouse pointer style**, choose **Custom**.
5. Choose another color and set it to pure red:

```text
R 255
G 0
B 0
Hex #ff0000
```

6. Increase pointer size if you want the cursor easier to find in red mode.

## Return the Cursor to White

When you turn RedShift off, use **Cursor Setup** again and choose the **White** pointer style in Windows Settings.

## Notes

- RedShift also asks the Windows Magnification API to show the system cursor while the red screen effect is active. Depending on Windows, GPU driver, and cursor type, the full-screen color matrix may tint the pointer automatically.
- The manual Accessibility setting is the most reliable way to make every cursor shape red, including text-select and resize cursors.
