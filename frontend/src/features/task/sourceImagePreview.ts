/**
 * 源图预览列表的锚点数据模型。
 */

export interface SourceImageListItem {
  readonly name: string;
  readonly pageKey: string;
}

export function imageNameToPageKey(name: string): string {
  return name.split("/").pop() ?? name;
}

export function imageNameToListItem(name: string): SourceImageListItem {
  return {
    name,
    pageKey: imageNameToPageKey(name),
  };
}
