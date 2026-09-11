# 依赖补丁

CivetWeb 固定提交 588860e30721bf5453b0440c390865a8e85dcae5 的 get_request 中，Transfer-Encoding 判断括号不匹配，并在改用 h_chunk/h_len 后仍引用不存在的 cl。GCC 15.2 无法编译该提交。

本项目只修正该块的括号、将 Transfer-Encoding 相关 cl 改为 h_chunk、Content-Length 相关 cl 改为 h_len，并拒绝 Content-Length 的非数字尾部与 ERANGE 溢出。保持现有的 Transfer-Encoding 与 Content-Length 冲突拒绝逻辑。

scripts/fetch_dependencies.py 保存可重复应用的精确文本补丁，原始源码位于 artifacts/backups/initial-build/civetweb.upstream.c，原始下载包和 SHA256 记录位于归档及 dependencies.lock.json。此补丁是本地适配，未声称已获上游合并。
