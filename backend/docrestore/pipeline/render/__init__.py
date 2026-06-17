# Copyright 2026 @lyty1997
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""输入源渲染层：把非图片输入（PDF 等）逐页渲染成 PNG，供既有图片链路消费。"""

from docrestore.pipeline.render.pdf import render_pdf_to_dir, safe_pdf_stem

__all__ = ["render_pdf_to_dir", "safe_pdf_stem"]
