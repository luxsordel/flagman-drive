import { File, Paths } from 'expo-file-system';
import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';
import * as ImagePicker from 'expo-image-picker';
import { Platform } from 'react-native';

export type PhotoSource = 'camera' | 'library';

export type PickResult =
  { kind: 'picked'; uri: string } | { kind: 'canceled' } | { kind: 'cameraDenied' };

// Enough to read a document, and keeps any photo well below the API's 10 MB limit.
const maxSide = 2560;
const jpegQuality = 0.85;

/**
 * Takes or picks a document photo and returns a JPEG copy in the app cache. The camera does not
 * save the shot to the gallery; the caller deletes the copy with `discardPhoto` once it is sent.
 */
export async function pickPhoto(
  source: PhotoSource,
  { selfie = false }: { selfie?: boolean } = {},
): Promise<PickResult> {
  const options: ImagePicker.ImagePickerOptions = {
    mediaTypes: ['images'],
    quality: 1,
    exif: false,
    ...(selfie ? { cameraType: ImagePicker.CameraType.front } : {}),
  };
  let result: ImagePicker.ImagePickerResult;
  if (source === 'camera') {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) return { kind: 'cameraDenied' };
    result = await ImagePicker.launchCameraAsync(options);
  } else {
    result = await ImagePicker.launchImageLibraryAsync(options);
  }
  const asset = result.canceled ? undefined : result.assets[0];
  if (asset === undefined) return { kind: 'canceled' };
  try {
    return { kind: 'picked', uri: await toJpeg(asset) };
  } finally {
    // The picker's own copy: the original stays wherever the client keeps it.
    discardPhoto(asset.uri);
  }
}

/**
 * Deletes a photo this app put in its cache. Anything outside the cache is not ours to delete,
 * so it is left alone; failures are ignored because the system clears the cache eventually.
 */
export function discardPhoto(uri: string): void {
  try {
    if (Platform.OS === 'web') {
      if (uri.startsWith('blob:')) URL.revokeObjectURL(uri);
      return;
    }
    if (isInCache(uri)) new File(uri).delete();
  } catch {
    // Already gone or locked by the image view; nothing else to do.
  }
}

/**
 * One spelling for the many names of the same local file: with or without `file://`,
 * percent-encoded, iOS `/private/var/…` for `/var/…`, Android `/data/data/…` for `/data/user/0/…`.
 * Null for anything that is not a plain absolute path, `..` included.
 */
export function canonicalPath(uri: string): string | null {
  let path = uri.replace(/^file:(\/\/)?/, '');
  try {
    path = decodeURI(path);
  } catch {
    return null;
  }
  if (!path.startsWith('/') || path.split('/').includes('..')) return null;
  return path.replace(/^\/private\/var\//, '/var/').replace(/^\/data\/data\//, '/data/user/0/');
}

function isInCache(uri: string): boolean {
  const file = canonicalPath(uri);
  const cache = canonicalPath(Paths.cache.uri);
  if (file === null || cache === null) return false;
  return file.startsWith(cache.endsWith('/') ? cache : `${cache}/`);
}

// HEIC from an iPhone is not accepted by the API (ADR-0012), and a full-size shot is far larger
// than a document needs.
async function toJpeg({ uri, width, height }: ImagePicker.ImagePickerAsset): Promise<string> {
  const context = ImageManipulator.manipulate(uri);
  try {
    if (Math.max(width, height) > maxSide) {
      context.resize(width >= height ? { width: maxSide } : { height: maxSide });
    }
    const image = await context.renderAsync();
    try {
      const saved = await image.saveAsync({ format: SaveFormat.JPEG, compress: jpegQuality });
      return saved.uri;
    } finally {
      image.release();
    }
  } finally {
    context.release();
  }
}
