# Migration Manifest

## 等价重构约束说明

本次重构属于**等价拆分重构**，不是功能重写、产品改版或体验重设计。
后续逐文件迁移时，必须始终满足以下约束：

1. 不得增减原项目任何功能
2. 不得改变原项目 UI 的页面结构、布局、交互流程、按钮行为、文案含义与视觉组织逻辑
3. 不得擅自优化、重设计、重命名业务概念或替换已有行为
4. 允许做的仅包括：
   - 文件拆分
   - 模块归类
   - import 调整
   - 为保持运行所需的最小桥接
5. 后续逐文件填充代码时，也必须严格遵守“功能等价、UI 等价、行为等价”原则

## 目标目录结构

- app/
- app/core/
- app/storage/
- app/utils/
- app/services/
- app/api/
- app/ui/
- app/ui/common/
- app/ui/components/
- app/ui/pages/
- app/ui/dialogs/
- app/jobs/

## 每个文件职责

- `app/main.py`：应用入口与最终装配
- `app/core/config.py`：路径常量、环境变量、脚本模板、静态大常量
- `app/core/state.py`：全局缓存与 UI 状态容器
- `app/core/logging.py`：logger、线程池、进程池、scheduler
- `app/core/security.py`：鉴权与会话安全基础逻辑
- `app/storage/files.py`：底层文件写入与锁
- `app/storage/repositories.py`：配置/缓存存取仓储
- `app/storage/bootstrap.py`：初始化加载
- `app/utils/encoding.py`：链接/编码处理
- `app/utils/formatters.py`：格式化与排序辅助
- `app/utils/geo.py`：Geo/IP/地区识别
- `app/utils/network.py`：DNS、origin、网络辅助
- `app/utils/async_tools.py`：后台执行器包装
- `app/services/*.py`：SSH、Cloudflare、XUI、Probe、订阅、仪表盘、服务器操作等服务逻辑
- `app/api/*.py`：认证、探针、订阅、状态、通知接口桥接
- `app/ui/common/*.py`：通知与公共设置对话框
- `app/ui/components/*.py`：侧边栏、行组件、仪表盘组件、状态卡片
- `app/ui/pages/*.py`：登录页、主页面、探针页、订阅页、内容路由、单机页、聚合页、公共状态页
- `app/ui/dialogs/*.py`：服务器、入站、订阅、分组、批量、部署、SSH 终端等对话框
- `app/jobs/*.py`：监控、流量、GeoIP、启动任务

## 每个文件对应的 main.py 函数/类清单

- `app/core/config.py`：路径常量、环境变量、脚本模板、大型静态映射
- `app/core/state.py`：各类缓存、UI refs、刷新锁、页面状态、告警计数
- `app/core/logging.py`：logger、BG_EXECUTOR、scheduler
- `app/core/security.py`：check_auth
- `app/storage/files.py`：_save_file_sync_internal、safe_save、FILE_LOCK
- `app/storage/repositories.py`：load_global_key、save_global_key、save_servers、save_admin_config、save_subs、save_nodes_cache
- `app/storage/bootstrap.py`：init_data
- `app/utils/encoding.py`：parse_vless_link_to_node、safe_base64、decode_base64_safe、generate_converted_link、generate_node_link、generate_detail_config
- `app/utils/formatters.py`：format_bytes、format_uptime、cn_to_arabic_str、to_safe_sort_list、smart_sort_key
- `app/utils/geo.py`：fetch_geo_from_ip、get_coords_from_name、get_flag_for_country、auto_prepend_flag、detect_country_group、get_echarts_region_name
- `app/utils/network.py`：sync_ping_worker、get_dynamic_origin、_resolve_dns_bg、get_real_ip_display
- `app/utils/async_tools.py`：run_in_bg_executor
- `app/services/ssh.py`：get_ssh_client、get_ssh_client_sync、_ssh_exec_wrapper、_exec、WebSSH
- `app/services/cloudflare.py`：CloudflareHandler
- `app/services/xui_api.py`：XUIManager
- `app/services/xui_ssh.py`：SSHXUIManager
- `app/services/manager_factory.py`：get_manager
- `app/services/xui_fetch.py`：fetch_inbounds_safe
- `app/services/probe.py`：record_ping_history、install_probe_on_server、batch_install_all_probes、get_server_status、batch_ping_nodes、probe_push_data、probe_register、smart_detect_ssh_user_task、auto_register_node
- `app/services/deployment.py`：open_deploy_xhttp_dialog、open_deploy_hysteria_dialog、open_deploy_snell_dialog
- `app/services/subscriptions.py`：sub_handler、group_sub_handler、short_group_handler、short_sub_handler、copy_group_link
- `app/services/dashboard.py`：prepare_map_data、get_dashboard_live_data、calculate_dashboard_data
- `app/services/server_ops.py`：force_geoip_naming_task、generate_smart_name、silent_refresh_all、fast_resolve_single_server、get_all_groups、save_server_config、get_targets_by_scope
- `app/api/auth.py`：login_page 相关桥接
- `app/api/probe.py`：probe_register、probe_push_data、auto_register_node
- `app/api/subscriptions.py`：sub_handler、group_sub_handler、short_group_handler、short_sub_handler
- `app/api/status.py`：status_page_router
- `app/api/notifications.py`：send_telegram_message
- `app/ui/common/notifications.py`：safe_notify、show_loading、safe_copy_to_clipboard
- `app/ui/common/dialogs_settings.py`：open_cloudflare_settings_dialog、open_probe_settings_dialog
- `app/ui/common/dialogs_data.py`：open_global_settings_dialog、open_data_mgmt_dialog
- `app/ui/components/sidebar.py`：on_server_click_handler、render_single_sidebar_row、render_sidebar_content
- `app/ui/components/server_rows.py`：bind_ip_label、draw_row、show_custom_node_info
- `app/ui/components/dashboard.py`：refresh_dashboard_ui、load_dashboard_stats
- `app/ui/components/status_cards.py`：render_status_card
- `app/ui/pages/login_page.py`：login_page
- `app/ui/pages/main_page.py`：main_page
- `app/ui/pages/probe_page.py`：render_probe_page
- `app/ui/pages/subs_page.py`：load_subs_view
- `app/ui/pages/content_router.py`：refresh_content、_render_ui_internal、get_targets_by_scope
- `app/ui/pages/single_server.py`：render_single_server_view
- `app/ui/pages/aggregated_view.py`：render_aggregated_view
- `app/ui/pages/public_status.py`：open_mobile_server_detail、open_pc_server_detail、is_mobile_device、status_page_router、render_desktop_status_page、render_mobile_status_page
- `app/ui/dialogs/server_dialog.py`：open_server_dialog
- `app/ui/dialogs/inbound_dialog.py`：InboundEditor、open_inbound_dialog、delete_inbound、delete_inbound_with_confirm
- `app/ui/dialogs/sub_dialogs.py`：SubEditor、open_sub_editor、AdvancedSubEditor、open_advanced_sub_editor
- `app/ui/dialogs/group_dialogs.py`：open_quick_group_create_dialog、open_group_sort_dialog、open_unified_group_manager、open_combined_group_management、open_create_group_dialog
- `app/ui/dialogs/bulk_edit.py`：BulkEditor、open_bulk_edit_dialog
- `app/ui/dialogs/batch_ssh.py`：BatchSSH
- `app/ui/dialogs/deploy_xhttp.py`：open_deploy_xhttp_dialog
- `app/ui/dialogs/deploy_hysteria.py`：open_deploy_hysteria_dialog
- `app/ui/dialogs/deploy_snell.py`：open_deploy_snell_dialog
- `app/ui/dialogs/ssh_console.py`：open_ssh_interface
- `app/jobs/monitor.py`：job_monitor_status
- `app/jobs/traffic.py`：job_sync_all_traffic
- `app/jobs/geoip.py`：job_check_geo_ip、force_geoip_naming_task
- `app/jobs/startup.py`：startup_sequence

## 推荐迁移顺序

1. `app/core/config.py`
2. `app/core/state.py`
3. `app/core/logging.py`
4. `app/storage/files.py`
5. `app/storage/repositories.py`
6. `app/storage/bootstrap.py`
7. `app/utils/encoding.py`
8. `app/utils/formatters.py`
9. `app/utils/geo.py`
10. `app/utils/network.py`
11. `app/utils/async_tools.py`
12. `app/services/ssh.py`
13. `app/services/cloudflare.py`
14. `app/services/xui_api.py`
15. `app/services/xui_ssh.py`
16. `app/services/manager_factory.py`
17. `app/services/xui_fetch.py`
18. `app/services/probe.py`
19. `app/services/server_ops.py`
20. `app/services/dashboard.py`
21. `app/api/subscriptions.py`
22. `app/api/probe.py`
23. `app/api/notifications.py`
24. `app/ui/common/notifications.py`
25. `app/ui/common/dialogs_settings.py`
26. `app/ui/common/dialogs_data.py`
27. `app/ui/dialogs/inbound_dialog.py`
28. `app/ui/dialogs/sub_dialogs.py`
29. `app/ui/dialogs/group_dialogs.py`
30. `app/ui/dialogs/bulk_edit.py`
31. `app/ui/dialogs/batch_ssh.py`
32. `app/ui/components/server_rows.py`
33. `app/ui/components/dashboard.py`
34. `app/ui/components/status_cards.py`
35. `app/ui/pages/probe_page.py`
36. `app/ui/pages/subs_page.py`
37. `app/ui/pages/content_router.py`
38. `app/ui/pages/aggregated_view.py`
39. `app/ui/components/sidebar.py`
40. `app/ui/dialogs/server_dialog.py`
41. `app/ui/dialogs/deploy_xhttp.py`
42. `app/ui/dialogs/deploy_hysteria.py`
43. `app/ui/dialogs/deploy_snell.py`
44. `app/ui/dialogs/ssh_console.py`
45. `app/ui/pages/single_server.py`
46. `app/ui/pages/login_page.py`
47. `app/ui/pages/main_page.py`
48. `app/ui/pages/public_status.py`
49. `app/jobs/monitor.py`
50. `app/jobs/traffic.py`
51. `app/jobs/geoip.py`
52. `app/jobs/startup.py`
53. `app/api/auth.py`
54. `app/api/status.py`
55. `app/main.py`
