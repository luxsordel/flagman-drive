import { describe, expect, it } from 'vitest';

import { viewerLayout } from './viewerLayout';

/**
 * The box the turned photo covers, in units of the frame width; it has to match the frame,
 * otherwise part of the photo sticks out where the stage cannot scroll.
 */
function coveredBox(ratio: number, rotation: number) {
  const { frame, image } = viewerLayout(ratio, 1, rotation);
  const imageWidth = Number.parseFloat(String(image.width)) / 100;
  const imageHeight = imageWidth / ratio;
  const sideways = rotation % 180 !== 0;
  return {
    covered: sideways ? imageHeight / imageWidth : imageWidth / imageHeight,
    coveredWidth: sideways ? imageHeight : imageWidth,
    frameRatio: Number(frame.aspectRatio),
  };
}

describe('photo viewer layout', () => {
  it.each([0, 90, 180, 270])('fits a landscape photo turned by %i° into the frame', (rotation) => {
    const { covered, coveredWidth, frameRatio } = coveredBox(4 / 3, rotation);

    expect(coveredWidth).toBeCloseTo(1);
    expect(covered).toBeCloseTo(frameRatio);
  });

  it('fits a portrait photo turned sideways', () => {
    const { covered, coveredWidth, frameRatio } = coveredBox(3 / 4, 90);

    expect(coveredWidth).toBeCloseTo(1);
    expect(covered).toBeCloseTo(frameRatio);
  });

  it('zooms the frame and turns the photo around its center', () => {
    const { frame, image } = viewerLayout(4 / 3, 2.5, 270);

    expect(frame.width).toBe('250%');
    expect(image.transform).toBe('translate(-50%, -50%) rotate(270deg)');
  });
});
