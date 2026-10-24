import type { CSSProperties } from 'react';

export const minZoom = 1;
export const maxZoom = 4;
export const zoomStep = 0.5;

export interface ViewerLayout {
  /** Takes the place of the photo as the operator sees it, so the stage scrolls to all of it. */
  frame: CSSProperties;
  /** Centered in the frame and turned there; a transform alone would leave its old place. */
  image: CSSProperties;
}

/**
 * Sizes for a photo with the `width / height` ratio at `zoom` (1 fits the stage width) and turned
 * by `rotation` degrees, a multiple of 90. Turned sideways, the frame swaps its sides, and the photo
 * is as wide as the frame is tall.
 */
export function viewerLayout(ratio: number, zoom: number, rotation: number): ViewerLayout {
  const sideways = rotation % 180 !== 0;
  return {
    frame: {
      width: `${String(zoom * 100)}%`,
      aspectRatio: sideways ? 1 / ratio : ratio,
    },
    image: {
      width: sideways ? `${String(ratio * 100)}%` : '100%',
      transform: `translate(-50%, -50%) rotate(${String(rotation)}deg)`,
    },
  };
}
