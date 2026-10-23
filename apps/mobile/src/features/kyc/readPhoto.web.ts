/** The picked photo on the web is a blob or data URI; the browser sends a Blob as is. */
export async function readPhoto(uri: string): Promise<Blob> {
  const response = await fetch(uri);
  return response.blob();
}
