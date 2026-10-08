import useMediaQuery from "@mui/material/useMediaQuery";

/** Exit time of overlays opened by the address: 240 ms, or a 120 ms fade under reduced motion. */
export function useOverlayExitMs() {
  return useMediaQuery("(prefers-reduced-motion: reduce)") ? 120 : 240;
}

/** MUI Dialog timing: 240 ms in, 180 ms out; 120 ms fade under reduced motion. */
export function useDialogDuration() {
  return useOverlayExitMs() === 120 ? 120 : { enter: 240, exit: 180 };
}

