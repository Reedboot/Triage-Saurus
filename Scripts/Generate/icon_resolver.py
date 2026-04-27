#!/usr/bin/env python3
"""Icon resolver: maps cloud resource types to icon files and generates data URIs.

Provides icon lookup and caching for architecture diagram visualization with
cloud provider icons (Azure, AWS, GCP).
"""

import re
import base64
from pathlib import Path
from typing import Optional, Dict, Set
from functools import lru_cache


# Icon folder structure: web/static/assets/icons/{provider}/{category}/
ICONS_ROOT = Path(__file__).parent.parent.parent / "web" / "static" / "assets" / "icons"


# Mapping of Azure resource types to icon categories and preferred icon names
# Auto-generated from all available icons (588 resources)
AZURE_RESOURCE_TYPE_TO_ICON = {
    'azurerm_a': ('other', 'a'),
    'azurerm_abs_member': ('blockchain', 'abs-member'),
    'azurerm_active_directory_connect_health': ('identity', 'active-directory-connect-health'),
    'azurerm_activity_log': ('monitor', 'activity-log'),
    'azurerm_ad_b2c': ('identity', 'ad-b2c'),
    'azurerm_administrative_units': ('identity', 'administrative-units'),
    'azurerm_advisor': ('management_governance', 'advisor'),
    'azurerm_ai_at_edge': ('new_icons', 'ai-at-edge'),
    'azurerm_ai_studio': ('ai_machine_learning', 'ai-studio'),
    'azurerm_aks': ('other', 'aks'),
    'azurerm_alerts': ('management_governance', 'alerts'),
    'azurerm_all_resources': ('general', 'all-resources'),
    'azurerm_analysis_services': ('analytics', 'analysis-services'),
    'azurerm_anomaly_detector': ('ai_machine_learning', 'anomaly-detector'),
    'azurerm_api_center': ('web', 'api-center'),
    'azurerm_api_connections': ('web', 'api-connections'),
    'azurerm_api_for_fhir': ('integration', 'api-for-fhir'),
    'azurerm_api_proxy': ('identity', 'api-proxy'),
    'azurerm_apim': ('web', 'apim'),
    'azurerm_app_compliance_automation': ('other', 'app-compliance-automation'),
    'azurerm_app_configuration': ('integration', 'app-configuration'),
    'azurerm_app_gateway': ('networking', 'app-gateway'),
    'azurerm_app_insights': ('monitor', 'app-insights'),
    'azurerm_app_registration': ('other', 'app-registration'),
    'azurerm_app_service': ('web', 'app-service'),
    'azurerm_app_service_certificate': ('app_services', 'app-service-certificate'),
    'azurerm_app_service_domain': ('app_services', 'app-service-domain'),
    'azurerm_app_service_domains': ('web', 'app-service-domains'),
    'azurerm_app_service_environment': ('app_services', 'app-service-environment'),
    'azurerm_app_service_environments': ('web', 'app-service-environments'),
    'azurerm_app_service_plan': ('web', 'app-service-plan'),
    'azurerm_app_services': ('web', 'app-services'),
    'azurerm_app_space': ('web', 'app-space'),
    'azurerm_app_space_component': ('web', 'app-space-component'),
    'azurerm_app_testing': ('new_icons', 'app-testing'),
    'azurerm_applens': ('azure_ecosystem', 'applens'),
    'azurerm_application_group': ('compute', 'application-group'),
    'azurerm_application_security_groups': ('security', 'application-security-groups'),
    'azurerm_applied_ai_services': ('ai_machine_learning', 'applied-ai-services'),
    'azurerm_aquila': ('other', 'aquila'),
    'azurerm_arc': ('management_governance', 'arc'),
    'azurerm_arc_data_services': ('other', 'arc-data-services'),
    'azurerm_arc_kubernetes': ('other', 'arc-kubernetes'),
    'azurerm_arc_machines': ('management_governance', 'arc-machines'),
    'azurerm_arc_sql_managed_instance': ('other', 'arc-sql-managed-instance'),
    'azurerm_atm_multistack': ('networking', 'atm-multistack'),
    'azurerm_auto_scale': ('monitor', 'auto-scale'),
    'azurerm_automanaged_vm': ('compute', 'automanaged-vm'),
    'azurerm_automation': ('management_governance', 'automation'),
    'azurerm_availability_set': ('compute', 'availability-set'),
    'azurerm_avs_vm': ('other', 'avs-vm'),
    'azurerm_azure_devops': ('devops', 'azure-devops'),
    'azurerm_azure_monitors_for_sap_solutions': ('monitor', 'azure-monitors-for-sap-solutions'),
    'azurerm_azureattestation': ('other', 'azureattestation'),
    'azurerm_azurite': ('other', 'azurite'),
    'azurerm_backlog': ('general', 'backlog'),
    'azurerm_backup_center': ('other', 'backup-center'),
    'azurerm_backup_vault': ('other', 'backup-vault'),
    'azurerm_bare_metal_infrastructure': ('other', 'bare-metal-infrastructure'),
    'azurerm_bastions': ('networking', 'bastions'),
    'azurerm_batch_accounts': ('containers', 'batch-accounts'),
    'azurerm_batch_ai': ('ai_machine_learning', 'batch-ai'),
    'azurerm_biz_talk': ('general', 'biz-talk'),
    'azurerm_blob_block': ('general', 'blob-block'),
    'azurerm_blob_page': ('general', 'blob-page'),
    'azurerm_blockchain_applications': ('blockchain', 'blockchain-applications'),
    'azurerm_blockchain_service': ('blockchain', 'blockchain-service'),
    'azurerm_blueprints': ('management_governance', 'blueprints'),
    'azurerm_bonsai': ('ai_machine_learning', 'bonsai'),
    'azurerm_bot_services': ('ai_machine_learning', 'bot-services'),
    'azurerm_branch': ('general', 'branch'),
    'azurerm_breeze': ('new_icons', 'breeze'),
    'azurerm_browser': ('general', 'browser'),
    'azurerm_bug': ('general', 'bug'),
    'azurerm_builds': ('general', 'builds'),
    'azurerm_business_process_tracking': ('integration', 'business-process-tracking'),
    'azurerm_cache': ('storage', 'cache'),
    'azurerm_capacity': ('azure_stack', 'capacity'),
    'azurerm_capacity_reservation_groups': ('other', 'capacity-reservation-groups'),
    'azurerm_cdn': ('web', 'cdn'),
    'azurerm_center_for_sap': ('other', 'center-for-sap'),
    'azurerm_central_service_instance_for_sap': ('other', 'central-service-instance-for-sap'),
    'azurerm_ceres': ('other', 'ceres'),
    'azurerm_change_analysis': ('monitor', 'change-analysis'),
    'azurerm_chaos_studio': ('other', 'chaos-studio'),
    'azurerm_client_apps': ('intune', 'client-apps'),
    'azurerm_cloud_services_classic': ('compute', 'cloud-services-classic'),
    'azurerm_cloud_services_extended_support': ('other', 'cloud-services-extended-support'),
    'azurerm_cloud_shell': ('other', 'cloud-shell'),
    'azurerm_cloudtest': ('devops', 'cloudtest'),
    'azurerm_code': ('general', 'code'),
    'azurerm_code_optimization': ('devops', 'code-optimization'),
    'azurerm_cognitive_search': ('web', 'cognitive-search'),
    'azurerm_cognitive_services': ('web', 'cognitive-services'),
    'azurerm_cognitive_services_decisions': ('ai_machine_learning', 'cognitive-services-decisions'),
    'azurerm_collaborative_service': ('azure_ecosystem', 'collaborative-service'),
    'azurerm_commit': ('general', 'commit'),
    'azurerm_community_images': ('other', 'community-images'),
    'azurerm_compliance': ('management_governance', 'compliance'),
    'azurerm_compliance_center': ('other', 'compliance-center'),
    'azurerm_compute_fleet': ('compute', 'compute-fleet'),
    'azurerm_compute_galleries': ('other', 'compute-galleries'),
    'azurerm_computer_vision': ('ai_machine_learning', 'computer-vision'),
    'azurerm_conditional_access': ('security', 'conditional-access'),
    'azurerm_confidential_ledgers': ('other', 'confidential-ledgers'),
    'azurerm_connected_vehicle_platform': ('other', 'connected-vehicle-platform'),
    'azurerm_connections': ('networking', 'connections'),
    'azurerm_consortium': ('blockchain', 'consortium'),
    'azurerm_consumption_commitment': ('new_icons', 'consumption-commitment'),
    'azurerm_container_apps_environments': ('other', 'container-apps-environments'),
    'azurerm_container_instances': ('containers', 'container-instances'),
    'azurerm_container_registries': ('containers', 'container-registries'),
    'azurerm_container_services_deprecated': ('compute', 'container-services-deprecated'),
    'azurerm_container_storage': ('new_icons', 'container-storage'),
    'azurerm_content_moderators': ('ai_machine_learning', 'content-moderators'),
    'azurerm_content_safety': ('ai_machine_learning', 'content-safety'),
    'azurerm_controls': ('general', 'controls'),
    'azurerm_controls_horizontal': ('general', 'controls-horizontal'),
    'azurerm_cosmos_db': ('iot', 'cosmos-db'),
    'azurerm_cost_alerts': ('general', 'cost-alerts'),
    'azurerm_cost_analysis': ('general', 'cost-analysis'),
    'azurerm_cost_budgets': ('general', 'cost-budgets'),
    'azurerm_cost_export': ('other', 'cost-export'),
    'azurerm_cost_management': ('general', 'cost-management'),
    'azurerm_cost_management_and_billing': ('migrate', 'cost-management-and-billing'),
    'azurerm_counter': ('general', 'counter'),
    'azurerm_cubes': ('general', 'cubes'),
    'azurerm_custom_vision': ('ai_machine_learning', 'custom-vision'),
    'azurerm_customer_lockbox_for_microsoft_azure': ('management_governance', 'customer-lockbox-for-microsoft-azure'),
    'azurerm_dashboard': ('general', 'dashboard'),
    'azurerm_dashboard_hub': ('other', 'dashboard-hub'),
    'azurerm_data_box': ('storage', 'data-box'),
    'azurerm_data_catalog': ('integration', 'data-catalog'),
    'azurerm_data_collection_rules': ('other', 'data-collection-rules'),
    'azurerm_data_explorer_clusters': ('databases', 'data-explorer-clusters'),
    'azurerm_data_factories': ('integration', 'data-factories'),
    'azurerm_data_lake': ('storage', 'data-lake'),
    'azurerm_data_lake_store_gen1': ('analytics', 'data-lake-store-gen1'),
    'azurerm_data_share_invitations': ('storage', 'data-share-invitations'),
    'azurerm_data_shares': ('storage', 'data-shares'),
    'azurerm_data_virtualization': ('new_icons', 'data-virtualization'),
    'azurerm_database_instance_for_sap': ('other', 'database-instance-for-sap'),
    'azurerm_database_mariadb_server': ('databases', 'database-mariadb-server'),
    'azurerm_database_migration_services': ('migration', 'database-migration-services'),
    'azurerm_databox_gateway': ('storage', 'databox-gateway'),
    'azurerm_databricks': ('analytics', 'databricks'),
    'azurerm_ddos_protection_plans': ('networking', 'ddos-protection-plans'),
    'azurerm_dedicated_hsm': ('other', 'dedicated-hsm'),
    'azurerm_defender_cm_local_manager': ('other', 'defender-cm-local-manager'),
    'azurerm_defender_dcs_controller': ('other', 'defender-dcs-controller'),
    'azurerm_defender_distributer_control_system': ('other', 'defender-distributer-control-system'),
    'azurerm_defender_engineering_station': ('other', 'defender-engineering-station'),
    'azurerm_defender_external_management': ('other', 'defender-external-management'),
    'azurerm_defender_freezer_monitor': ('other', 'defender-freezer-monitor'),
    'azurerm_defender_historian': ('other', 'defender-historian'),
    'azurerm_defender_hmi': ('other', 'defender-hmi'),
    'azurerm_defender_industrial_packaging_system': ('other', 'defender-industrial-packaging-system'),
    'azurerm_defender_industrial_printer': ('other', 'defender-industrial-printer'),
    'azurerm_defender_industrial_robot': ('other', 'defender-industrial-robot'),
    'azurerm_defender_industrial_scale_system': ('other', 'defender-industrial-scale-system'),
    'azurerm_defender_marquee': ('other', 'defender-marquee'),
    'azurerm_defender_meter': ('other', 'defender-meter'),
    'azurerm_defender_plc': ('other', 'defender-plc'),
    'azurerm_defender_pneumatic_device': ('other', 'defender-pneumatic-device'),
    'azurerm_defender_programable_board': ('other', 'defender-programable-board'),
    'azurerm_defender_relay': ('other', 'defender-relay'),
    'azurerm_defender_robot_controller': ('other', 'defender-robot-controller'),
    'azurerm_defender_rtu': ('other', 'defender-rtu'),
    'azurerm_defender_sensor': ('other', 'defender-sensor'),
    'azurerm_defender_slot': ('other', 'defender-slot'),
    'azurerm_defender_web_guiding_system': ('other', 'defender-web-guiding-system'),
    'azurerm_deployment_environments': ('other', 'deployment-environments'),
    'azurerm_detonation': ('security', 'detonation'),
    'azurerm_dev_console': ('general', 'dev-console'),
    'azurerm_dev_tunnels': ('other', 'dev-tunnels'),
    'azurerm_device_compliance': ('intune', 'device-compliance'),
    'azurerm_device_configuration': ('intune', 'device-configuration'),
    'azurerm_device_enrollment': ('intune', 'device-enrollment'),
    'azurerm_device_provisioning_services': ('iot', 'device-provisioning-services'),
    'azurerm_device_security_apple': ('intune', 'device-security-apple'),
    'azurerm_device_security_google': ('intune', 'device-security-google'),
    'azurerm_device_security_windows': ('intune', 'device-security-windows'),
    'azurerm_device_update_iot_hub': ('other', 'device-update-iot-hub'),
    'azurerm_devices': ('intune', 'devices'),
    'azurerm_devops_starter': ('devops', 'devops-starter'),
    'azurerm_devtest_labs': ('devops', 'devtest-labs'),
    'azurerm_diagnostics_settings': ('monitor', 'diagnostics-settings'),
    'azurerm_digital_twins': ('iot', 'digital-twins'),
    'azurerm_disk': ('other', 'disk'),
    'azurerm_disk_encryption': ('compute', 'disk-encryption'),
    'azurerm_disks': ('compute', 'disks'),
    'azurerm_disks_classic': ('compute', 'disks-classic'),
    'azurerm_dns_multistack': ('networking', 'dns-multistack'),
    'azurerm_dns_private_resolver': ('networking', 'dns-private-resolver'),
    'azurerm_dns_security_policy': ('networking', 'dns-security-policy'),
    'azurerm_dns_zones': ('networking', 'dns-zones'),
    'azurerm_download': ('general', 'download'),
    'azurerm_ebooks': ('intune', 'ebooks'),
    'azurerm_edge_actions': ('new_icons', 'edge-actions'),
    'azurerm_edge_hardware_center': ('other', 'edge-hardware-center'),
    'azurerm_edge_management': ('other', 'edge-management'),
    'azurerm_edge_storage_accelerator': ('new_icons', 'edge-storage-accelerator'),
    'azurerm_education': ('management_governance', 'education'),
    'azurerm_elastic_job_agents': ('databases', 'elastic-job-agents'),
    'azurerm_elastic_san': ('other', 'elastic-san'),
    'azurerm_endpoint_analytics': ('analytics', 'endpoint-analytics'),
    'azurerm_engage_center_connect': ('new_icons', 'engage-center-connect'),
    'azurerm_enterprise_applications': ('identity', 'enterprise-applications'),
    'azurerm_entra_connect': ('identity', 'entra-connect'),
    'azurerm_entra_connect_health': ('identity', 'entra-connect-health'),
    'azurerm_entra_connect_sync': ('identity', 'entra-connect-sync'),
    'azurerm_entra_domain_services': ('identity', 'entra-domain-services'),
    'azurerm_entra_global_secure_access': ('identity', 'entra-global-secure-access'),
    'azurerm_entra_id_protection': ('identity', 'entra-id-protection'),
    'azurerm_entra_identity_custom_roles': ('identity', 'entra-identity-custom-roles'),
    'azurerm_entra_identity_licenses': ('other', 'entra-identity-licenses'),
    'azurerm_entra_identity_risky_signins': ('security', 'entra-identity-risky-signins'),
    'azurerm_entra_identity_risky_users': ('security', 'entra-identity-risky-users'),
    'azurerm_entra_identity_roles_and_administrators': ('intune', 'entra-identity-roles-and-administrators'),
    'azurerm_entra_internet_access': ('identity', 'entra-internet-access'),
    'azurerm_entra_managed_identities': ('identity', 'entra-managed-identities'),
    'azurerm_entra_private_access': ('identity', 'entra-private-access'),
    'azurerm_entra_privleged_identity_management': ('identity', 'entra-privleged-identity-management'),
    'azurerm_entra_verified_id': ('identity', 'entra-verified-id'),
    'azurerm_error': ('general', 'error'),
    'azurerm_event_grid': ('integration', 'event-grid'),
    'azurerm_event_grid_domains': ('integration', 'event-grid-domains'),
    'azurerm_event_hub': ('iot', 'event-hub'),
    'azurerm_event_hub_clusters': ('iot', 'event-hub-clusters'),
    'azurerm_exchange_access': ('intune', 'exchange-access'),
    'azurerm_exchange_on_premises_access': ('other', 'exchange-on-premises-access'),
    'azurerm_experimentation_studio': ('ai_machine_learning', 'experimentation-studio'),
    'azurerm_express_route': ('other', 'express-route'),
    'azurerm_expressroute_circuits': ('networking', 'expressroute-circuits'),
    'azurerm_expressroute_direct': ('other', 'expressroute-direct'),
    'azurerm_extendedsecurityupdates': ('security', 'extendedsecurityupdates'),
    'azurerm_extensions': ('general', 'extensions'),
    'azurerm_external_id': ('new_icons', 'external-id'),
    'azurerm_external_id_modified': ('new_icons', 'external-id-modified'),
    'azurerm_external_identities': ('identity', 'external-identities'),
    'azurerm_face_apis': ('ai_machine_learning', 'face-apis'),
    'azurerm_feature_previews': ('general', 'feature-previews'),
    'azurerm_fhir_service': ('other', 'fhir-service'),
    'azurerm_fiji': ('other', 'fiji'),
    'azurerm_file': ('general', 'file'),
    'azurerm_files': ('general', 'files'),
    'azurerm_fileshares': ('storage', 'fileshares'),
    'azurerm_firewall': ('networking', 'firewall'),
    'azurerm_folder_blank': ('general', 'folder-blank'),
    'azurerm_folder_website': ('general', 'folder-website'),
    'azurerm_form_recognizers': ('ai_machine_learning', 'form-recognizers'),
    'azurerm_frd_qa': ('new_icons', 'frd-qa'),
    'azurerm_free_services': ('general', 'free-services'),
    'azurerm_front_door_and_cdn_profiles': ('networking', 'front-door-and-cdn-profiles'),
    'azurerm_ftp': ('general', 'ftp'),
    'azurerm_function_app': ('iot', 'function-app'),
    'azurerm_gear': ('general', 'gear'),
    'azurerm_genomics': ('ai_machine_learning', 'genomics'),
    'azurerm_genomics_accounts': ('ai_machine_learning', 'genomics-accounts'),
    'azurerm_globe_error': ('general', 'globe-error'),
    'azurerm_globe_success': ('general', 'globe-success'),
    'azurerm_globe_warning': ('general', 'globe-warning'),
    'azurerm_groups': ('identity', 'groups'),
    'azurerm_guide': ('general', 'guide'),
    'azurerm_hd_insight_clusters': ('analytics', 'hd-insight-clusters'),
    'azurerm_hdi_aks_cluster': ('other', 'hdi-aks-cluster'),
    'azurerm_heart': ('general', 'heart'),
    'azurerm_help_and_support': ('general', 'help-and-support'),
    'azurerm_host_groups': ('compute', 'host-groups'),
    'azurerm_host_pools': ('compute', 'host-pools'),
    'azurerm_hosts': ('compute', 'hosts'),
    'azurerm_hpc_workbenches': ('other', 'hpc-workbenches'),
    'azurerm_hybrid_center': ('azure_ecosystem', 'hybrid-center'),
    'azurerm_hybrid_connectivity_hub': ('new_icons', 'hybrid-connectivity-hub'),
    'azurerm_icm_troubleshooting': ('other', 'icm-troubleshooting'),
    'azurerm_identity_governance': ('identity', 'identity-governance'),
    'azurerm_identity_secure_score': ('security', 'identity-secure-score'),
    'azurerm_image': ('general', 'image'),
    'azurerm_image_definitions': ('compute', 'image-definitions'),
    'azurerm_image_template': ('compute', 'image-template'),
    'azurerm_image_versions': ('compute', 'image-versions'),
    'azurerm_images': ('compute', 'images'),
    'azurerm_immersive_readers': ('ai_machine_learning', 'immersive-readers'),
    'azurerm_import_export_jobs': ('storage', 'import-export-jobs'),
    'azurerm_industrial_iot': ('iot', 'industrial-iot'),
    'azurerm_information': ('general', 'information'),
    'azurerm_information_protection': ('security', 'information-protection'),
    'azurerm_infrastructure_backup': ('azure_stack', 'infrastructure-backup'),
    'azurerm_input_output': ('general', 'input-output'),
    'azurerm_instance_pools': ('other', 'instance-pools'),
    'azurerm_integration_accounts': ('integration', 'integration-accounts'),
    'azurerm_integration_environments': ('integration', 'integration-environments'),
    'azurerm_integration_service_environments': ('integration', 'integration-service-environments'),
    'azurerm_internet_analyzer_profiles': ('other', 'internet-analyzer-profiles'),
    'azurerm_intune': ('intune', 'intune'),
    'azurerm_intune_app_protection': ('intune', 'intune-app-protection'),
    'azurerm_intune_for_education': ('intune', 'intune-for-education'),
    'azurerm_intune_trends': ('management_governance', 'intune-trends'),
    'azurerm_iot_central_applications': ('iot', 'iot-central-applications'),
    'azurerm_iot_edge': ('iot', 'iot-edge'),
    'azurerm_iot_hub': ('iot', 'iot-hub'),
    'azurerm_iot_operations': ('iot', 'iot-operations'),
    'azurerm_ip_address_manager': ('networking', 'ip-address-manager'),
    'azurerm_ip_groups': ('networking', 'ip-groups'),
    'azurerm_journey_hub': ('general', 'journey-hub'),
    'azurerm_key_vault': ('security', 'key-vault'),
    'azurerm_keys': ('menu', 'keys'),
    'azurerm_kubernetes': ('compute', 'kubernetes'),
    'azurerm_kubernetes_fleet_manager': ('other', 'kubernetes-fleet-manager'),
    'azurerm_kubernetes_hub': ('new_icons', 'kubernetes-hub'),
    'azurerm_kubernetes_service': ('containers', 'kubernetes-service'),
    'azurerm_lab_accounts': ('devops', 'lab-accounts'),
    'azurerm_lab_services': ('devops', 'lab-services'),
    'azurerm_landing_zone': ('new_icons', 'landing-zone'),
    'azurerm_language': ('ai_machine_learning', 'language'),
    'azurerm_language_understanding': ('ai_machine_learning', 'language-understanding'),
    'azurerm_launch_portal': ('general', 'launch-portal'),
    'azurerm_learn': ('general', 'learn'),
    'azurerm_lighthouse': ('management_governance', 'lighthouse'),
    'azurerm_linux': ('new_icons', 'linux'),
    'azurerm_load_balancer': ('new_icons', 'load-balancer'),
    'azurerm_load_test': ('general', 'load-test'),
    'azurerm_load_testing': ('other', 'load-testing'),
    'azurerm_local': ('new_icons', 'local'),
    'azurerm_local_network_gateways': ('other', 'local-network-gateways'),
    'azurerm_location': ('general', 'location'),
    'azurerm_log_analytics': ('other', 'log-analytics'),
    'azurerm_log_streaming': ('general', 'log-streaming'),
    'azurerm_logic_app': ('new_icons', 'logic-app'),
    'azurerm_logic_apps_custom_connector': ('integration', 'logic-apps-custom-connector'),
    'azurerm_machine_learning': ('ai_machine_learning', 'machine-learning'),
    'azurerm_machine_learning_studio_classic_web_services': ('iot', 'machine-learning-studio-classic-web-services'),
    'azurerm_machine_learning_studio_web_service_plans': ('iot', 'machine-learning-studio-web-service-plans'),
    'azurerm_machine_learning_studio_workspaces': ('iot', 'machine-learning-studio-workspaces'),
    'azurerm_machinesazurearc': ('management_governance', 'machinesazurearc'),
    'azurerm_maintenance_configuration': ('compute', 'maintenance-configuration'),
    'azurerm_managed_applications_center': ('management_governance', 'managed-applications-center'),
    'azurerm_managed_database': ('databases', 'managed-database'),
    'azurerm_managed_desktop': ('management_governance', 'managed-desktop'),
    'azurerm_managed_devops_pools': ('devops', 'managed-devops-pools'),
    'azurerm_managed_file_shares': ('storage', 'managed-file-shares'),
    'azurerm_managed_grafana': ('other', 'managed-grafana'),
    'azurerm_managed_identities': ('identity', 'managed-identities'),
    'azurerm_managed_instance_apache_cassandra': ('other', 'managed-instance-apache-cassandra'),
    'azurerm_managed_service_fabric': ('compute', 'managed-service-fabric'),
    'azurerm_management_groups': ('general', 'management-groups'),
    'azurerm_management_portal': ('general', 'management-portal'),
    'azurerm_maps_accounts': ('iot', 'maps-accounts'),
    'azurerm_marketplace': ('general', 'marketplace'),
    'azurerm_marketplace_management': ('general', 'marketplace-management'),
    'azurerm_media': ('general', 'media'),
    'azurerm_media_file': ('general', 'media-file'),
    'azurerm_media_service': ('web', 'media-service'),
    'azurerm_medtech_service': ('other', 'medtech-service'),
    'azurerm_mesh_applications': ('compute', 'mesh-applications'),
    'azurerm_metrics': ('monitor', 'metrics'),
    'azurerm_metrics_advisor': ('compute', 'metrics-advisor'),
    'azurerm_microsoft_defender_easm': ('security', 'microsoft-defender-easm'),
    'azurerm_microsoft_defender_for_cloud': ('security', 'microsoft-defender-for-cloud'),
    'azurerm_microsoft_defender_for_iot': ('security', 'microsoft-defender-for-iot'),
    'azurerm_microsoft_dev_box': ('other', 'microsoft-dev-box'),
    'azurerm_microsoft_discovery': ('new_icons', 'microsoft-discovery'),
    'azurerm_migrate': ('migrate', 'migrate'),
    'azurerm_mindaro': ('intune', 'mindaro'),
    'azurerm_mission_landing_zone': ('other', 'mission-landing-zone'),
    'azurerm_mobile': ('general', 'mobile'),
    'azurerm_mobile_engagement': ('general', 'mobile-engagement'),
    'azurerm_mobile_networks': ('other', 'mobile-networks'),
    'azurerm_modular_data_center': ('other', 'modular-data-center'),
    'azurerm_module': ('general', 'module'),
    'azurerm_monitor': ('other', 'monitor'),
    'azurerm_monitor_health_models': ('other', 'monitor-health-models'),
    'azurerm_multi_factor_authentication': ('identity', 'multi-factor-authentication'),
    'azurerm_multi_tenancy': ('azure_stack', 'multi-tenancy'),
    'azurerm_multifactor_authentication': ('security', 'multifactor-authentication'),
    'azurerm_my_customers': ('management_governance', 'my-customers'),
    'azurerm_nat': ('networking', 'nat'),
    'azurerm_netapp_files': ('storage', 'netapp-files'),
    'azurerm_network_foundation_hub': ('new_icons', 'network-foundation-hub'),
    'azurerm_network_function_manager': ('other', 'network-function-manager'),
    'azurerm_network_function_manager_functions': ('other', 'network-function-manager-functions'),
    'azurerm_network_interface': ('other', 'network-interface'),
    'azurerm_network_interfaces': ('networking', 'network-interfaces'),
    'azurerm_network_managers': ('other', 'network-managers'),
    'azurerm_network_security_hub': ('new_icons', 'network-security-hub'),
    'azurerm_network_security_perimeters': ('other', 'network-security-perimeters'),
    'azurerm_network_watcher': ('networking', 'network-watcher'),
    'azurerm_notification_hub_namespaces': ('web', 'notification-hub-namespaces'),
    'azurerm_notification_hubs': ('mobile', 'notification-hubs'),
    'azurerm_nsg': ('networking', 'nsg'),
    'azurerm_object_understanding': ('ai_machine_learning', 'object-understanding'),
    'azurerm_offers': ('azure_stack', 'offers'),
    'azurerm_on_premises_data_gateways': ('networking', 'on-premises-data-gateways'),
    'azurerm_open_supply_chain_platform': ('other', 'open-supply-chain-platform'),
    'azurerm_openai': ('ai_machine_learning', 'openai'),
    'azurerm_operation_center': ('new_icons', 'operation-center'),
    'azurerm_operation_log_classic': ('management_governance', 'operation-log-classic'),
    'azurerm_operator_5g_core': ('hybrid_multicloud', 'operator-5g-core'),
    'azurerm_operator_insights': ('hybrid_multicloud', 'operator-insights'),
    'azurerm_operator_nexus': ('hybrid_multicloud', 'operator-nexus'),
    'azurerm_operator_service_manager': ('hybrid_multicloud', 'operator-service-manager'),
    'azurerm_oracle_database': ('databases', 'oracle-database'),
    'azurerm_orbital': ('other', 'orbital'),
    'azurerm_os_images_classic': ('compute', 'os-images-classic'),
    'azurerm_osconfig': ('other', 'osconfig'),
    'azurerm_outbound_connection': ('blockchain', 'outbound-connection'),
    'azurerm_partner_namespace': ('integration', 'partner-namespace'),
    'azurerm_partner_registration': ('integration', 'partner-registration'),
    'azurerm_partner_topic': ('integration', 'partner-topic'),
    'azurerm_peering_service': ('other', 'peering-service'),
    'azurerm_peerings': ('other', 'peerings'),
    'azurerm_personalizers': ('ai_machine_learning', 'personalizers'),
    'azurerm_planetary_computer_pro': ('new_icons', 'planetary-computer-pro'),
    'azurerm_plans': ('azure_stack', 'plans'),
    'azurerm_policy': ('management_governance', 'policy'),
    'azurerm_postgresql': ('other', 'postgresql'),
    'azurerm_power': ('general', 'power'),
    'azurerm_power_bi_embedded': ('analytics', 'power-bi-embedded'),
    'azurerm_power_platform': ('web', 'power-platform'),
    'azurerm_power_up': ('general', 'power-up'),
    'azurerm_powershell': ('general', 'powershell'),
    'azurerm_preview_features': ('general', 'preview-features'),
    'azurerm_private_endpoints': ('other', 'private-endpoints'),
    'azurerm_private_link': ('networking', 'private-link'),
    'azurerm_private_link_service': ('networking', 'private-link-service'),
    'azurerm_private_link_services': ('networking', 'private-link-services'),
    'azurerm_process_explorer': ('general', 'process-explorer'),
    'azurerm_production_ready_database': ('general', 'production-ready-database'),
    'azurerm_programmable_connectivity': ('hybrid_multicloud', 'programmable-connectivity'),
    'azurerm_promethus': ('new_icons', 'promethus'),
    'azurerm_proximity_placement_groups': ('networking', 'proximity-placement-groups'),
    'azurerm_public_ip': ('other', 'public-ip'),
    'azurerm_pubsub': ('new_icons', 'pubsub'),
    'azurerm_qna_makers': ('ai_machine_learning', 'qna-makers'),
    'azurerm_quickstart_center': ('general', 'quickstart-center'),
    'azurerm_quotas': ('other', 'quotas'),
    'azurerm_recent': ('general', 'recent'),
    'azurerm_recovery_services_vaults': ('storage', 'recovery-services-vaults'),
    'azurerm_red_hat_openshift': ('containers', 'red-hat-openshift'),
    'azurerm_redis': ('new_icons', 'redis'),
    'azurerm_region_management': ('general', 'region-management'),
    'azurerm_relays': ('integration', 'relays'),
    'azurerm_remote_rendering': ('mixed_reality', 'remote-rendering'),
    'azurerm_reservations': ('general', 'reservations'),
    'azurerm_reserved_capacity': ('other', 'reserved-capacity'),
    'azurerm_reserved_ip_addresses_classic': ('networking', 'reserved-ip-addresses-classic'),
    'azurerm_resource_explorer': ('general', 'resource-explorer'),
    'azurerm_resource_graph_explorer': ('management_governance', 'resource-graph-explorer'),
    'azurerm_resource_group': ('general', 'resource-group'),
    'azurerm_resource_group_list': ('general', 'resource-group-list'),
    'azurerm_resource_guard': ('other', 'resource-guard'),
    'azurerm_resource_linked': ('general', 'resource-linked'),
    'azurerm_resource_management_private_link': ('networking', 'resource-management-private-link'),
    'azurerm_resource_mover': ('other', 'resource-mover'),
    'azurerm_resources_provider': ('management_governance', 'resources-provider'),
    'azurerm_restore_points': ('compute', 'restore-points'),
    'azurerm_restore_points_collections': ('compute', 'restore-points-collections'),
    'azurerm_route_filters': ('networking', 'route-filters'),
    'azurerm_route_tables': ('networking', 'route-tables'),
    'azurerm_rtos': ('other', 'rtos'),
    'azurerm_savings_plans': ('other', 'savings-plans'),
    'azurerm_scheduled_actions': ('new_icons', 'scheduled-actions'),
    'azurerm_scheduler': ('general', 'scheduler'),
    'azurerm_scheduler_job_collections': ('management_governance', 'scheduler-job-collections'),
    'azurerm_scvmm_management_servers': ('other', 'scvmm-management-servers'),
    'azurerm_search': ('general', 'search'),
    'azurerm_search_grid': ('general', 'search-grid'),
    'azurerm_security': ('identity', 'security'),
    'azurerm_security_baselines': ('intune', 'security-baselines'),
    'azurerm_sendgrid_accounts': ('integration', 'sendgrid-accounts'),
    'azurerm_sentinel': ('security', 'sentinel'),
    'azurerm_server_farm': ('general', 'server-farm'),
    'azurerm_serverless_search': ('ai_machine_learning', 'serverless-search'),
    'azurerm_service_bus': ('integration', 'service-bus'),
    'azurerm_service_catalog_mad': ('management_governance', 'service-catalog-mad'),
    'azurerm_service_endpoint_policies': ('networking', 'service-endpoint-policies'),
    'azurerm_service_fabric_clusters': ('containers', 'service-fabric-clusters'),
    'azurerm_service_groups': ('new_icons', 'service-groups'),
    'azurerm_service_health': ('general', 'service-health'),
    'azurerm_service_providers': ('management_governance', 'service-providers'),
    'azurerm_shared_image_galleries': ('compute', 'shared-image-galleries'),
    'azurerm_signalr': ('web', 'signalr'),
    'azurerm_software_as_a_service': ('integration', 'software-as-a-service'),
    'azurerm_software_updates': ('intune', 'software-updates'),
    'azurerm_solutions': ('management_governance', 'solutions'),
    'azurerm_sonic_dash': ('other', 'sonic-dash'),
    'azurerm_spatial_anchor_accounts': ('mixed_reality', 'spatial-anchor-accounts'),
    'azurerm_speech_services': ('ai_machine_learning', 'speech-services'),
    'azurerm_sphere': ('other', 'sphere'),
    'azurerm_spot_vm': ('networking', 'spot-vm'),
    'azurerm_spring_apps': ('web', 'spring-apps'),
    'azurerm_sql': ('databases', 'sql'),
    'azurerm_sql_data_warehouses': ('integration', 'sql-data-warehouses'),
    'azurerm_sql_database': ('new_icons', 'sql-database'),
    'azurerm_sql_edge': ('databases', 'sql-edge'),
    'azurerm_sql_elastic_pools': ('databases', 'sql-elastic-pools'),
    'azurerm_sql_managed_instance': ('databases', 'sql-managed-instance'),
    'azurerm_sql_server': ('other', 'sql-server'),
    'azurerm_ssd': ('general', 'ssd'),
    'azurerm_ssh_keys': ('other', 'ssh-keys'),
    'azurerm_ssis_lift_and_shift_ir': ('databases', 'ssis-lift-and-shift-ir'),
    'azurerm_stack': ('iot', 'stack'),
    'azurerm_stack_edge': ('storage', 'stack-edge'),
    'azurerm_stack_hci_premium': ('iot', 'stack-hci-premium'),
    'azurerm_stack_hci_sizer': ('iot', 'stack-hci-sizer'),
    'azurerm_stage_maps': ('new_icons', 'stage-maps'),
    'azurerm_static_apps': ('web', 'static-apps'),
    'azurerm_storage_account': ('storage', 'storage-account'),
    'azurerm_storage_actions': ('storage', 'storage-actions'),
    'azurerm_storage_azure_files': ('general', 'storage-azure-files'),
    'azurerm_storage_container': ('general', 'storage-container'),
    'azurerm_storage_explorer': ('storage', 'storage-explorer'),
    'azurerm_storage_functions': ('other', 'storage-functions'),
    'azurerm_storage_hubs': ('new_icons', 'storage-hubs'),
    'azurerm_storage_mover': ('other', 'storage-mover'),
    'azurerm_storage_queue': ('general', 'storage-queue'),
    'azurerm_storage_sync_services': ('storage', 'storage-sync-services'),
    'azurerm_storsimple_data_managers': ('storage', 'storsimple-data-managers'),
    'azurerm_storsimple_device_managers': ('storage', 'storsimple-device-managers'),
    'azurerm_stream_analytics_jobs': ('iot', 'stream-analytics-jobs'),
    'azurerm_subnet': ('networking', 'subnet'),
    'azurerm_support_center_blue': ('other', 'support-center-blue'),
    'azurerm_sustainability': ('other', 'sustainability'),
    'azurerm_synapse_analytics': ('databases', 'synapse-analytics'),
    'azurerm_system_topic': ('integration', 'system-topic'),
    'azurerm_table': ('general', 'table'),
    'azurerm_tag': ('general', 'tag'),
    'azurerm_tags': ('general', 'tags'),
    'azurerm_targets_management': ('other', 'targets-management'),
    'azurerm_template_specs': ('other', 'template-specs'),
    'azurerm_templates': ('general', 'templates'),
    'azurerm_tenant_properties': ('identity', 'tenant-properties'),
    'azurerm_tenant_status': ('intune', 'tenant-status'),
    'azurerm_test_base': ('other', 'test-base'),
    'azurerm_tfs_vc_repository': ('general', 'tfs-vc-repository'),
    'azurerm_time_series_data_sets': ('iot', 'time-series-data-sets'),
    'azurerm_time_series_insights_access_policies': ('iot', 'time-series-insights-access-policies'),
    'azurerm_time_series_insights_environments': ('iot', 'time-series-insights-environments'),
    'azurerm_time_series_insights_event_sources': ('iot', 'time-series-insights-event-sources'),
    'azurerm_token_service': ('blockchain', 'token-service'),
    'azurerm_toolbox': ('general', 'toolbox'),
    'azurerm_toolchain_orchestrator': ('new_icons', 'toolchain-orchestrator'),
    'azurerm_traffic_manager': ('networking', 'traffic-manager'),
    'azurerm_translator_text': ('ai_machine_learning', 'translator-text'),
    'azurerm_troubleshoot': ('general', 'troubleshoot'),
    'azurerm_universal_print': ('management_governance', 'universal-print'),
    'azurerm_update_management_center': ('other', 'update-management-center'),
    'azurerm_updates': ('azure_stack', 'updates'),
    'azurerm_user_privacy': ('management_governance', 'user-privacy'),
    'azurerm_user_settings': ('security', 'user-settings'),
    'azurerm_users': ('identity', 'users'),
    'azurerm_verifiable_credentials': ('identity', 'verifiable-credentials'),
    'azurerm_verification_as_a_service': ('identity', 'verification-as-a-service'),
    'azurerm_versions': ('general', 'versions'),
    'azurerm_video_analyzers': ('other', 'video-analyzers'),
    'azurerm_video_indexer': ('other', 'video-indexer'),
    'azurerm_virtual_clusters': ('databases', 'virtual-clusters'),
    'azurerm_virtual_desktop': ('other', 'virtual-desktop'),
    'azurerm_virtual_enclaves': ('other', 'virtual-enclaves'),
    'azurerm_virtual_instance_for_sap': ('other', 'virtual-instance-for-sap'),
    'azurerm_virtual_machine': ('other', 'virtual-machine'),
    'azurerm_linux_virtual_machine': ('compute', 'virtual-machine'),
    'azurerm_windows_virtual_machine': ('compute', 'virtual-machine'),
    'azurerm_virtual_machine_extension': ('compute', 'virtual-machine'),
    'azurerm_managed_disk': ('compute', 'disk'),
    'azurerm_application_gateway': ('networking', 'load-balancer'),
    'azurerm_network_security_group': ('networking', 'nsg'),
    'azurerm_network_watcher_flow_log': ('networking', 'network-watcher'),
    'azurerm_mssql_server_security_alert_policy': ('security', 'microsoft-defender-for-cloud'),
    'azurerm_mysql_server': ('databases', 'sql'),
    'azurerm_postgresql_server': ('other', 'postgresql'),
    'azurerm_cosmosdb_account': ('databases', 'cosmos-db'),
    'azurerm_storage_blob': ('general', 'blob-block'),
    'azurerm_virtual_router': ('networking', 'virtual-router'),
    'azurerm_virtual_visits_builder': ('other', 'virtual-visits-builder'),
    'azurerm_virtual_wan_hub': ('networking', 'virtual-wan-hub'),
    'azurerm_virtual_wans': ('networking', 'virtual-wans'),
    'azurerm_vm_app_definitions': ('other', 'vm-app-definitions'),
    'azurerm_vm_app_versions': ('other', 'vm-app-versions'),
    'azurerm_vm_image_version': ('other', 'vm-image-version'),
    'azurerm_vm_images_classic': ('compute', 'vm-images-classic'),
    'azurerm_vm_scale_sets': ('compute', 'vm-scale-sets'),
    'azurerm_vnet': ('new_icons', 'vnet'),
    'azurerm_vpnclientwindows': ('new_icons', 'vpnclientwindows'),
    'azurerm_wac': ('other', 'wac'),
    'azurerm_wac_installer': ('other', 'wac-installer'),
    'azurerm_web_app': ('other', 'web-app'),
    'azurerm_web_jobs': ('other', 'web-jobs'),
    'azurerm_web_slots': ('general', 'web-slots'),
    'azurerm_web_test': ('general', 'web-test'),
    'azurerm_website_power': ('general', 'website-power'),
    'azurerm_website_staging': ('general', 'website-staging'),
    'azurerm_windows10_core_services': ('iot', 'windows10-core-services'),
    'azurerm_windows_notification_services': ('other', 'windows-notification-services'),
    'azurerm_workbooks': ('monitor', 'workbooks'),
    'azurerm_worker_container_app': ('other', 'worker-container-app'),
    'azurerm_workflow': ('general', 'workflow'),
    'azurerm_workload_orchestration': ('new_icons', 'workload-orchestration'),
    'azurerm_workspace_gateway': ('devops', 'workspace-gateway'),
    'azurerm_workspaces': ('compute', 'workspaces'),
}

# AWS resource type to icon mapping
# Auto-generated from all available icons (308 resources)
AWS_RESOURCE_TYPE_TO_ICON = {
    'aws_account': ('general', 'account'),
    'aws_activate': ('Arch_Customer-Enablement', 'activate'),
    'aws_amazon_s3_on_outposts_64': ('Arch_Storage', 'amazon-s3-on-outposts-64'),
    'aws_amplify': ('Arch_Front-End-Web-Mobile', 'amplify'),
    'aws_apache_mxnet_on_aws': ('Arch_Artificial-Intelligence', 'apache-mxnet-on-aws'),
    'aws_api_gateway': ('Arch_Networking-Content-Delivery', 'api-gateway'),
    'aws_app_mesh': ('Arch_Networking-Content-Delivery', 'app-mesh'),
    'aws_app_runner': ('Arch_Compute', 'app-runner'),
    'aws_app_studio': ('Arch_Artificial-Intelligence', 'app-studio'),
    'aws_appconfig': ('Arch_Management-Tools', 'appconfig'),
    'aws_appfabric': ('Arch_Business-Applications', 'appfabric'),
    'aws_appflow': ('Arch_Application-Integration', 'appflow'),
    'aws_application_discovery_service': ('Arch_Migration-Modernization', 'application-discovery-service'),
    'aws_application_migration_service': ('Arch_Migration-Modernization', 'application-migration-service'),
    'aws_application_recovery_controller': ('Arch_Networking-Content-Delivery', 'application-recovery-controller'),
    'aws_appsync': ('Arch_Application-Integration', 'appsync'),
    'aws_artifact': ('Arch_Security-Identity', 'artifact'),
    'aws_athena': ('Arch_Analytics', 'athena'),
    'aws_audit_manager': ('Arch_Security-Identity', 'audit-manager'),
    'aws_augmented_ai_a2i': ('Arch_Artificial-Intelligence', 'augmented-ai-a2i'),
    'aws_aurora': ('Arch_Databases', 'aurora'),
    'aws_autoscaling': ('general', 'autoscaling'),
    'aws_aws_auto_scaling_64': ('Arch_Management-Tools', 'aws-auto-scaling-64'),
    'aws_b2b_data_interchange': ('Arch_Application-Integration', 'b2b-data-interchange'),
    'aws_backint_agent': ('Arch_Management-Tools', 'backint-agent'),
    'aws_backup': ('Arch_Storage', 'backup'),
    'aws_batch': ('Arch_Compute', 'batch'),
    'aws_beanstalk': ('Arch_Compute', 'beanstalk'),
    'aws_bedrock': ('Arch_Artificial-Intelligence', 'bedrock'),
    'aws_bedrock_agentcore': ('Arch_Artificial-Intelligence', 'bedrock-agentcore'),
    'aws_billing_conductor': ('Arch_Cloud-Financial-Management', 'billing-conductor'),
    'aws_bottlerocket': ('Arch_Compute', 'bottlerocket'),
    'aws_braket': ('Arch_Quantum-Technologies', 'braket'),
    'aws_budgets': ('Arch_Cloud-Financial-Management', 'budgets'),
    'aws_certificate_manager': ('Arch_Security-Identity', 'certificate-manager'),
    'aws_chatbot': ('Arch_Management-Tools', 'chatbot'),
    'aws_chime': ('Arch_Business-Applications', 'chime'),
    'aws_chime_sdk': ('Arch_Business-Applications', 'chime-sdk'),
    'aws_clean_rooms': ('Arch_Analytics', 'clean-rooms'),
    'aws_client_vpn': ('Arch_Networking-Content-Delivery', 'client-vpn'),
    'aws_cloud': ('general', 'cloud'),
    'aws_cloud9': ('Arch_Developer-Tools', 'cloud9'),
    'aws_cloud_32': ('general', 'cloud-32'),
    'aws_cloud_control_api': ('Arch_Developer-Tools', 'cloud-control-api'),
    'aws_cloud_development_kit': ('Arch_Developer-Tools', 'cloud-development-kit'),
    'aws_cloud_directory': ('Arch_Security-Identity', 'cloud-directory'),
    'aws_cloud_logo': ('general', 'cloud-logo'),
    'aws_cloud_logo_32': ('general', 'cloud-logo-32'),
    'aws_cloud_map': ('Arch_Networking-Content-Delivery', 'cloud-map'),
    'aws_cloud_wan': ('Arch_Networking-Content-Delivery', 'cloud-wan'),
    'aws_cloudformation': ('Arch_Management-Tools', 'cloudformation'),
    'aws_cloudfront': ('Arch_Networking-Content-Delivery', 'cloudfront'),
    'aws_cloudhsm': ('Arch_Security-Identity', 'cloudhsm'),
    'aws_cloudsearch': ('Arch_Analytics', 'cloudsearch'),
    'aws_cloudshell': ('Arch_Developer-Tools', 'cloudshell'),
    'aws_cloudtrail': ('Arch_Management-Tools', 'cloudtrail'),
    'aws_cloudwatch': ('Arch_Management-Tools', 'cloudwatch'),
    'aws_codeartifact': ('Arch_Developer-Tools', 'codeartifact'),
    'aws_codebuild': ('Arch_Developer-Tools', 'codebuild'),
    'aws_codecatalyst': ('Arch_Developer-Tools', 'codecatalyst'),
    'aws_codecommit': ('Arch_Developer-Tools', 'codecommit'),
    'aws_codedeploy': ('Arch_Developer-Tools', 'codedeploy'),
    'aws_codeguru': ('Arch_Artificial-Intelligence', 'codeguru'),
    'aws_codepipeline': ('Arch_Developer-Tools', 'codepipeline'),
    'aws_codewhisperer': ('Arch_Artificial-Intelligence', 'codewhisperer'),
    'aws_cognito': ('Arch_Security-Identity', 'cognito'),
    'aws_command_line_interface': ('Arch_Developer-Tools', 'command-line-interface'),
    'aws_comprehend': ('Arch_Artificial-Intelligence', 'comprehend'),
    'aws_comprehend_medical': ('Arch_Artificial-Intelligence', 'comprehend-medical'),
    'aws_compute_optimizer': ('Arch_Management-Tools', 'compute-optimizer'),
    'aws_config': ('Arch_Management-Tools', 'config'),
    'aws_connect': ('Arch_Business-Applications', 'connect'),
    'aws_console_mobile_application': ('Arch_Management-Tools', 'console-mobile-application'),
    'aws_control_tower': ('Arch_Management-Tools', 'control-tower'),
    'aws_corporate_data_center': ('general', 'corporate-data-center'),
    'aws_corretto': ('Arch_Developer-Tools', 'corretto'),
    'aws_cost_and_usage_report': ('Arch_Cloud-Financial-Management', 'cost-and-usage-report'),
    'aws_cost_explorer': ('Arch_Cloud-Financial-Management', 'cost-explorer'),
    'aws_data_exchange': ('Arch_Analytics', 'data-exchange'),
    'aws_data_firehose': ('Arch_Analytics', 'data-firehose'),
    'aws_data_transfer_terminal': ('Arch_Migration-Modernization', 'data-transfer-terminal'),
    'aws_database_migration_service': ('Arch_Databases', 'database-migration-service'),
    'aws_datasync': ('Arch_Migration-Modernization', 'datasync'),
    'aws_datazone': ('Arch_Analytics', 'datazone'),
    'aws_dcv': ('Arch_Compute', 'dcv'),
    'aws_deadline_cloud': ('Arch_Media-Services', 'deadline-cloud'),
    'aws_deep_learning_amis': ('Arch_Artificial-Intelligence', 'deep-learning-amis'),
    'aws_deep_learning_containers': ('Arch_Artificial-Intelligence', 'deep-learning-containers'),
    'aws_deepracer': ('Arch_Artificial-Intelligence', 'deepracer'),
    'aws_detective': ('Arch_Security-Identity', 'detective'),
    'aws_device_farm': ('Arch_Front-End-Web-Mobile', 'device-farm'),
    'aws_devops_agent': ('Arch_Management-Tools', 'devops-agent'),
    'aws_devops_guru': ('Arch_Artificial-Intelligence', 'devops-guru'),
    'aws_direct_connect': ('Arch_Networking-Content-Delivery', 'direct-connect'),
    'aws_directory_service': ('Arch_Security-Identity', 'directory-service'),
    'aws_distro_for_opentelemetry': ('Arch_Management-Tools', 'distro-for-opentelemetry'),
    'aws_documentdb': ('Arch_Databases', 'documentdb'),
    'aws_dynamodb': ('Arch_Databases', 'dynamodb'),
    'aws_ec2': ('general', 'ec2'),
    'aws_ecr': ('Arch_Security-Identity', 'ecr'),
    'aws_ecs': ('Arch_Containers', 'ecs'),
    'aws_efs': ('Arch_Storage', 'efs'),
    'aws_eks': ('Arch_Containers', 'eks'),
    'aws_elastic_block_store': ('Arch_Storage', 'elastic-block-store'),
    'aws_elastic_container_registry': ('Arch_Containers', 'elastic-container-registry'),
    'aws_elastic_container_service': ('Arch_Containers', 'elastic-container-service'),
    'aws_elastic_disaster_recovery': ('Arch_Storage', 'elastic-disaster-recovery'),
    'aws_elastic_fabric_adapter': ('Arch_Compute', 'elastic-fabric-adapter'),
    'aws_elastic_inference': ('Arch_Artificial-Intelligence', 'elastic-inference'),
    'aws_elastic_kubernetes_service': ('Arch_Containers', 'elastic-kubernetes-service'),
    'aws_elastic_load_balancing': ('Arch_Networking-Content-Delivery', 'elastic-load-balancing'),
    'aws_elastic_vmware_service': ('Arch_Compute', 'elastic-vmware-service'),
    'aws_elasticache': ('Arch_Databases', 'elasticache'),
    'aws_elemental_appliances_&_software': ('Arch_Media-Services', 'elemental-appliances-&-software'),
    'aws_elemental_conductor': ('Arch_Media-Services', 'elemental-conductor'),
    'aws_elemental_delta': ('Arch_Media-Services', 'elemental-delta'),
    'aws_elemental_link': ('Arch_Media-Services', 'elemental-link'),
    'aws_elemental_live': ('Arch_Media-Services', 'elemental-live'),
    'aws_elemental_mediaconnect': ('Arch_Media-Services', 'elemental-mediaconnect'),
    'aws_elemental_mediaconvert': ('Arch_Media-Services', 'elemental-mediaconvert'),
    'aws_elemental_medialive': ('Arch_Media-Services', 'elemental-medialive'),
    'aws_elemental_mediapackage': ('Arch_Media-Services', 'elemental-mediapackage'),
    'aws_elemental_mediastore': ('Arch_Media-Services', 'elemental-mediastore'),
    'aws_elemental_mediatailor': ('Arch_Media-Services', 'elemental-mediatailor'),
    'aws_elemental_server': ('Arch_Media-Services', 'elemental-server'),
    'aws_emr': ('Arch_Analytics', 'emr'),
    'aws_end_user_messaging': ('Arch_Business-Applications', 'end-user-messaging'),
    'aws_entity_resolution': ('Arch_Analytics', 'entity-resolution'),
    'aws_eventbridge': ('Arch_Application-Integration', 'eventbridge'),
    'aws_express_workflows': ('Arch_Application-Integration', 'express-workflows'),
    'aws_fargate': ('Arch_Containers', 'fargate'),
    'aws_fault_injection_service': ('Arch_Developer-Tools', 'fault-injection-service'),
    'aws_file_cache': ('Arch_Storage', 'file-cache'),
    'aws_finspace': ('Arch_Analytics', 'finspace'),
    'aws_firewall_manager': ('Arch_Security-Identity', 'firewall-manager'),
    'aws_forecast': ('Arch_Artificial-Intelligence', 'forecast'),
    'aws_fraud_detector': ('Arch_Artificial-Intelligence', 'fraud-detector'),
    'aws_freertos': ('Arch_Internet-of-Things', 'freertos'),
    'aws_fsx': ('Arch_Storage', 'fsx'),
    'aws_fsx_for_lustre': ('Arch_Storage', 'fsx-for-lustre'),
    'aws_fsx_for_netapp_ontap': ('Arch_Storage', 'fsx-for-netapp-ontap'),
    'aws_fsx_for_openzfs': ('Arch_Storage', 'fsx-for-openzfs'),
    'aws_fsx_for_wfs': ('Arch_Storage', 'fsx-for-wfs'),
    'aws_gamelift_servers': ('Arch_Games', 'gamelift-servers'),
    'aws_gamelift_streams': ('Arch_Games', 'gamelift-streams'),
    'aws_global_accelerator': ('Arch_Networking-Content-Delivery', 'global-accelerator'),
    'aws_glue': ('Arch_Analytics', 'glue'),
    'aws_glue_databrew': ('Arch_Analytics', 'glue-databrew'),
    'aws_ground_station': ('Arch_Satellite', 'ground-station'),
    'aws_guardduty': ('Arch_Security-Identity', 'guardduty'),
    'aws_health_dashboard': ('Arch_Management-Tools', 'health-dashboard'),
    'aws_healthimaging': ('Arch_Artificial-Intelligence', 'healthimaging'),
    'aws_healthlake': ('Arch_Artificial-Intelligence', 'healthlake'),
    'aws_healthomics': ('Arch_Artificial-Intelligence', 'healthomics'),
    'aws_healthscribe': ('Arch_Artificial-Intelligence', 'healthscribe'),
    'aws_iam': ('Arch_Security-Identity', 'iam'),
    'aws_identity_and_access_management': ('Arch_Security-Identity', 'identity-and-access-management'),
    'aws_infrastructure_composer': ('Arch_Developer-Tools', 'infrastructure-composer'),
    'aws_inspector': ('Arch_Security-Identity', 'inspector'),
    'aws_interactive_video_service': ('Arch_Media-Services', 'interactive-video-service'),
    'aws_iot_core': ('Arch_Internet-of-Things', 'iot-core'),
    'aws_iot_device_defender': ('Arch_Internet-of-Things', 'iot-device-defender'),
    'aws_iot_device_management': ('Arch_Internet-of-Things', 'iot-device-management'),
    'aws_iot_events': ('Arch_Internet-of-Things', 'iot-events'),
    'aws_iot_expresslink': ('Arch_Internet-of-Things', 'iot-expresslink'),
    'aws_iot_fleetwise': ('Arch_Internet-of-Things', 'iot-fleetwise'),
    'aws_iot_greengrass': ('Arch_Internet-of-Things', 'iot-greengrass'),
    'aws_iot_greengrass_deployment': ('general', 'iot-greengrass-deployment'),
    'aws_iot_sitewise': ('Arch_Internet-of-Things', 'iot-sitewise'),
    'aws_iot_twinmaker': ('Arch_Internet-of-Things', 'iot-twinmaker'),
    'aws_iq': ('Arch_Customer-Enablement', 'iq'),
    'aws_kendra': ('Arch_Artificial-Intelligence', 'kendra'),
    'aws_key_management_service': ('Arch_Security-Identity', 'key-management-service'),
    'aws_keyspaces': ('Arch_Databases', 'keyspaces'),
    'aws_kinesis': ('Arch_Analytics', 'kinesis'),
    'aws_kinesis_data_streams': ('Arch_Analytics', 'kinesis-data-streams'),
    'aws_kinesis_video_streams': ('Arch_Media-Services', 'kinesis-video-streams'),
    'aws_lake_formation': ('Arch_Analytics', 'lake-formation'),
    'aws_lambda': ('Arch_Compute', 'lambda'),
    'aws_launch_wizard': ('Arch_Management-Tools', 'launch-wizard'),
    'aws_lex': ('Arch_Artificial-Intelligence', 'lex'),
    'aws_license_manager': ('Arch_Management-Tools', 'license-manager'),
    'aws_lightsail': ('Arch_Compute', 'lightsail'),
    'aws_lightsail_for_research': ('Arch_Compute', 'lightsail-for-research'),
    'aws_local_zones': ('Arch_Compute', 'local-zones'),
    'aws_location_service': ('Arch_Front-End-Web-Mobile', 'location-service'),
    'aws_lookout_for_equipment': ('Arch_Artificial-Intelligence', 'lookout-for-equipment'),
    'aws_lookout_for_vision': ('Arch_Artificial-Intelligence', 'lookout-for-vision'),
    'aws_macie': ('Arch_Security-Identity', 'macie'),
    'aws_mainframe_modernization': ('Arch_Migration-Modernization', 'mainframe-modernization'),
    'aws_managed_blockchain': ('Arch_Blockchain', 'managed-blockchain'),
    'aws_managed_grafana': ('Arch_Management-Tools', 'managed-grafana'),
    'aws_managed_service_for_apache_flink': ('Arch_Analytics', 'managed-service-for-apache-flink'),
    'aws_managed_service_for_prometheus': ('Arch_Management-Tools', 'managed-service-for-prometheus'),
    'aws_managed_services': ('Arch_Customer-Enablement', 'managed-services'),
    'aws_managed_streaming_for_apache_kafka': ('Arch_Analytics', 'managed-streaming-for-apache-kafka'),
    'aws_managed_workflows_for_apache_airflow': ('Arch_Application-Integration', 'managed-workflows-for-apache-airflow'),
    'aws_management_console': ('Arch_Management-Tools', 'management-console'),
    'aws_marketplace': ('Arch_General-Icons', 'marketplace'),
    'aws_marketplace_light': ('Arch_General-Icons', 'marketplace-light'),
    'aws_memorydb': ('Arch_Databases', 'memorydb'),
    'aws_migration_evaluator': ('Arch_Migration-Modernization', 'migration-evaluator'),
    'aws_migration_hub': ('Arch_Migration-Modernization', 'migration-hub'),
    'aws_monitron': ('Arch_Artificial-Intelligence', 'monitron'),
    'aws_mq': ('Arch_Application-Integration', 'mq'),
    'aws_neptune': ('Arch_Databases', 'neptune'),
    'aws_network_firewall': ('Arch_Security-Identity', 'network-firewall'),
    'aws_neuron': ('Arch_Artificial-Intelligence', 'neuron'),
    'aws_nitro_enclaves': ('Arch_Compute', 'nitro-enclaves'),
    'aws_nova': ('Arch_Artificial-Intelligence', 'nova'),
    'aws_open_3d_engine': ('Arch_Games', 'open-3d-engine'),
    'aws_opensearch_service': ('Arch_Analytics', 'opensearch-service'),
    'aws_oracle_database_at_aws': ('Arch_Databases', 'oracle-database-at-aws'),
    'aws_organizations': ('Arch_Management-Tools', 'organizations'),
    'aws_outposts_family': ('Arch_Compute', 'outposts-family'),
    'aws_outposts_rack': ('Arch_Compute', 'outposts-rack'),
    'aws_outposts_servers': ('Arch_Compute', 'outposts-servers'),
    'aws_panorama': ('Arch_Artificial-Intelligence', 'panorama'),
    'aws_parallel_cluster': ('Arch_Compute', 'parallel-cluster'),
    'aws_parallel_computing_service': ('Arch_Compute', 'parallel-computing-service'),
    'aws_partner_central': ('Arch_Management-Tools', 'partner-central'),
    'aws_payment_cryptography': ('Arch_Security-Identity', 'payment-cryptography'),
    'aws_personalize': ('Arch_Artificial-Intelligence', 'personalize'),
    'aws_pinpoint': ('Arch_Business-Applications', 'pinpoint'),
    'aws_pinpoint_apis': ('Arch_Business-Applications', 'pinpoint-apis'),
    'aws_polly': ('Arch_Artificial-Intelligence', 'polly'),
    'aws_private_certificate_authority': ('Arch_Security-Identity', 'private-certificate-authority'),
    'aws_private_subnet': ('general', 'private-subnet'),
    'aws_privatelink': ('Arch_Networking-Content-Delivery', 'privatelink'),
    'aws_professional_services': ('Arch_Customer-Enablement', 'professional-services'),
    'aws_proton': ('Arch_Management-Tools', 'proton'),
    'aws_public_subnet': ('general', 'public-subnet'),
    'aws_pytorch_on_aws': ('Arch_Artificial-Intelligence', 'pytorch-on-aws'),
    'aws_q': ('Arch_Artificial-Intelligence', 'q'),
    'aws_quick_suite': ('Arch_Business-Applications', 'quick-suite'),
    'aws_rds': ('Arch_Databases', 'rds'),
    'aws_red_hat_openshift_service_on_aws': ('Arch_Containers', 'red-hat-openshift-service-on-aws'),
    'aws_redshift': ('Arch_Analytics', 'redshift'),
    'aws_region': ('general', 'region'),
    'aws_rekognition': ('Arch_Artificial-Intelligence', 'rekognition'),
    'aws_repost': ('Arch_Customer-Enablement', 'repost'),
    'aws_repost_private': ('Arch_Customer-Enablement', 'repost-private'),
    'aws_reserved_instance_reporting': ('Arch_Cloud-Financial-Management', 'reserved-instance-reporting'),
    'aws_resilience_hub': ('Arch_Management-Tools', 'resilience-hub'),
    'aws_resource_access_manager': ('Arch_Security-Identity', 'resource-access-manager'),
    'aws_resource_explorer': ('Arch_Management-Tools', 'resource-explorer'),
    'aws_route53': ('Arch_Networking-Content-Delivery', 'route53'),
    'aws_rtb_fabric': ('Arch_Networking-Content-Delivery', 'rtb-fabric'),
    'aws_s3': ('Arch_Storage', 's3'),
    'aws_sagemaker': ('Arch_Analytics', 'sagemaker'),
    'aws_sagemaker_ai': ('Arch_Artificial-Intelligence', 'sagemaker-ai'),
    'aws_sagemaker_ground_truth': ('Arch_Artificial-Intelligence', 'sagemaker-ground-truth'),
    'aws_sagemaker_studio_lab': ('Arch_Artificial-Intelligence', 'sagemaker-studio-lab'),
    'aws_savings_plans': ('Arch_Cloud-Financial-Management', 'savings-plans'),
    'aws_security_agent': ('Arch_Security-Identity', 'security-agent'),
    'aws_security_hub': ('Arch_Security-Identity', 'security-hub'),
    'aws_security_incident_response': ('Arch_Security-Identity', 'security-incident-response'),
    'aws_security_lake': ('Arch_Security-Identity', 'security-lake'),
    'aws_server_contents': ('general', 'server-contents'),
    'aws_serverless_application_repository': ('Arch_Compute', 'serverless-application-repository'),
    'aws_service_catalog': ('Arch_Management-Tools', 'service-catalog'),
    'aws_service_management_connector': ('Arch_Management-Tools', 'service-management-connector'),
    'aws_shield': ('Arch_Security-Identity', 'shield'),
    'aws_signer': ('Arch_Security-Identity', 'signer'),
    'aws_simple_email_service': ('Arch_Business-Applications', 'simple-email-service'),
    'aws_simspace_weaver': ('Arch_Compute', 'simspace-weaver'),
    'aws_site_to_site_vpn': ('Arch_Networking-Content-Delivery', 'site-to-site-vpn'),
    'aws_snowball': ('Arch_Storage', 'snowball'),
    'aws_snowball_edge': ('Arch_Storage', 'snowball-edge'),
    'aws_sns': ('Arch_Application-Integration', 'sns'),
    'aws_spot_fleet': ('general', 'spot-fleet'),
    'aws_sqs': ('Arch_Application-Integration', 'sqs'),
    'aws_step_functions': ('Arch_Application-Integration', 'step-functions'),
    'aws_storage_gateway': ('Arch_Storage', 'storage-gateway'),
    'aws_supply_chain': ('Arch_Business-Applications', 'supply-chain'),
    'aws_support': ('Arch_Customer-Enablement', 'support'),
    'aws_systems_manager': ('Arch_Management-Tools', 'systems-manager'),
    'aws_telco_network_builder': ('Arch_Management-Tools', 'telco-network-builder'),
    'aws_tensorflow_on_aws': ('Arch_Artificial-Intelligence', 'tensorflow-on-aws'),
    'aws_textract': ('Arch_Artificial-Intelligence', 'textract'),
    'aws_thinkbox_deadline': ('Arch_Media-Services', 'thinkbox-deadline'),
    'aws_thinkbox_frost': ('Arch_Media-Services', 'thinkbox-frost'),
    'aws_thinkbox_krakatoa': ('Arch_Media-Services', 'thinkbox-krakatoa'),
    'aws_thinkbox_stoke': ('Arch_Media-Services', 'thinkbox-stoke'),
    'aws_thinkbox_xmesh': ('Arch_Media-Services', 'thinkbox-xmesh'),
    'aws_timestream': ('Arch_Databases', 'timestream'),
    'aws_tools_and_sdks': ('Arch_Developer-Tools', 'tools-and-sdks'),
    'aws_training_certification': ('Arch_Customer-Enablement', 'training-certification'),
    'aws_transcribe': ('Arch_Artificial-Intelligence', 'transcribe'),
    'aws_transfer_family': ('Arch_Migration-Modernization', 'transfer-family'),
    'aws_transform': ('Arch_Migration-Modernization', 'transform'),
    'aws_transit_gateway': ('Arch_Networking-Content-Delivery', 'transit-gateway'),
    'aws_translate': ('Arch_Artificial-Intelligence', 'translate'),
    'aws_trusted_advisor': ('Arch_Management-Tools', 'trusted-advisor'),
    'aws_user_notifications': ('Arch_Management-Tools', 'user-notifications'),
    'aws_verified_access': ('Arch_Networking-Content-Delivery', 'verified-access'),
    'aws_verified_permissions': ('Arch_Security-Identity', 'verified-permissions'),
    'aws_virtual_private_cloud': ('Arch_Networking-Content-Delivery', 'virtual-private-cloud'),
    'aws_vpc': ('general', 'vpc'),
    'aws_waf': ('Arch_Security-Identity', 'waf'),
    'aws_wavelength': ('Arch_Compute', 'wavelength'),
    'aws_well_architected_tool': ('Arch_Management-Tools', 'well-architected-tool'),
    'aws_wickr': ('Arch_Business-Applications', 'wickr'),
    'aws_workdocs': ('Arch_Business-Applications', 'workdocs'),
    'aws_workdocs_sdk': ('Arch_Business-Applications', 'workdocs-sdk'),
    'aws_workmail': ('Arch_Business-Applications', 'workmail'),
    'aws_workspaces': ('Arch_End-User-Computing', 'workspaces'),
    'aws_x_ray': ('Arch_Developer-Tools', 'x-ray'),
    # Terraform resource type aliases — map Terraform names to matching product icons
    'aws_instance':                   ('Arch_Compute', 'ec2'),
    'aws_lambda_function':            ('Arch_Compute', 'lambda'),
    'aws_s3_bucket':                  ('Arch_Storage', 's3'),
    'aws_s3_bucket_policy':           ('Arch_Storage', 's3'),
    'aws_s3_object':                  ('Arch_Storage', 's3'),
    'aws_s3_bucket_object':           ('Arch_Storage', 's3'),
    'aws_db_instance':                ('Arch_Databases', 'rds'),
    'aws_rds_cluster':                ('Arch_Databases', 'aurora'),
    'aws_ecr_repository':             ('Arch_Security-Identity', 'ecr'),
    'aws_elb':                        ('Arch_Networking-Content-Delivery', 'elastic-load-balancing'),
    'aws_neptune_cluster':            ('Arch_Databases', 'neptune'),
    'aws_network_interface':          ('Arch_Networking-Content-Delivery', 'virtual-private-cloud'),
    'aws_dynamodb_table':             ('Arch_Databases', 'dynamodb'),
    'aws_api_gateway_rest_api':       ('Arch_Networking-Content-Delivery', 'api-gateway'),
    'aws_api_gateway_v2_api':         ('Arch_Networking-Content-Delivery', 'api-gateway'),
    'aws_alb':                        ('Arch_Networking-Content-Delivery', 'elastic-load-balancing'),
    'aws_lb':                         ('Arch_Networking-Content-Delivery', 'elastic-load-balancing'),
    'aws_lb_listener':                ('Arch_Networking-Content-Delivery', 'elastic-load-balancing'),
    'aws_ecs_service':                ('Arch_Containers', 'ecs'),
    'aws_ecs_task_definition':        ('Arch_Containers', 'ecs'),
    'aws_ecs_cluster':                ('Arch_Containers', 'ecs'),
    'aws_eks_cluster':                ('Arch_Containers', 'elastic-kubernetes-service'),
    'aws_internet_gateway':           ('Arch_Networking-Content-Delivery', 'virtual-private-cloud'),
    'aws_subnet':                     ('Arch_Networking-Content-Delivery', 'virtual-private-cloud'),
    'aws_vpc':                        ('Arch_Networking-Content-Delivery', 'virtual-private-cloud'),
    'aws_security_group':             ('Arch_Security-Identity', 'network-firewall'),
    'aws_route_table':                ('Arch_Networking-Content-Delivery', 'route53'),
    'aws_secretsmanager_secret':      ('Arch_Security-Identity', 'key-management-service'),
    'aws_iam_role':                   ('Arch_Security-Identity', 'iam'),
    'aws_iam_policy':                 ('Arch_Security-Identity', 'iam'),
    'aws_cloudfront_distribution':    ('Arch_Networking-Content-Delivery', 'cloudfront'),
    'aws_elasticache_cluster':        ('Arch_Databases', 'elasticache'),
    'aws_sqs_queue':                  ('Arch_Application-Integration', 'sqs'),
    'aws_sns_topic':                  ('Arch_Application-Integration', 'sns'),
}

# GCP resource type to icon mapping
# Auto-generated from all available icons (19 resources)
GCP_RESOURCE_TYPE_TO_ICON = {
    'google_aihypercomputer': ('AI_Hypercomputer', 'aihypercomputer'),
    'google_alloydb': ('AlloyDB', 'alloydb'),
    'google_anthos': ('Anthos', 'anthos'),
    'google_apigee': ('Apigee', 'apigee'),
    'google_bigquery': ('BigQuery', 'bigquery'),
    'google_cloud_run': ('Cloud_Run', 'cloud-run'),
    'google_cloud_sql': ('Cloud_SQL', 'cloud-sql'),
    'google_cloud_storage': ('Cloud_Storage', 'cloud-storage'),
    'google_cloudspanner': ('Cloud_Spanner', 'cloudspanner'),
    'google_compute_engine': ('Compute_Engine', 'compute-engine'),
    'google_distributedcloud': ('Distributed_Cloud', 'distributedcloud'),
    'google_hyperdisk': ('Hyperdisk', 'hyperdisk'),
    'google_kubernetes_engine': ('GKE', 'kubernetes-engine'),
    'google_looker': ('Looker', 'looker'),
    'google_mandiant': ('Mandiant', 'mandiant'),
    'google_secops': ('Security_Operations', 'secops'),
    'google_securitycommandcenter': ('Security_Command_Center', 'securitycommandcenter'),
    'google_threatintelligence': ('Threat_Intelligence', 'threatintelligence'),
    'google_vertexai': ('Vertex_AI', 'vertexai'),
    # Terraform resource type aliases — map Terraform resource names to available GCP icons
    'google_compute_instance':           ('Compute_Engine', 'compute-engine'),
    'google_compute_firewall':           ('Compute_Engine', 'compute-engine'),
    'google_compute_network':            ('Compute_Engine', 'compute-engine'),
    'google_compute_subnetwork':         ('Compute_Engine', 'compute-engine'),
    'google_storage_bucket':             ('Cloud_Storage', 'cloud-storage'),
    'google_storage_bucket_object':      ('Cloud_Storage', 'cloud-storage'),
    'google_cloudfunctions_function':    ('Cloud_Run', 'cloud-run'),
    'google_cloudfunctions2_function':   ('Cloud_Run', 'cloud-run'),
    'google_app_engine_application':     ('Cloud_Run', 'cloud-run'),
    'google_cloud_run_service':          ('Cloud_Run', 'cloud-run'),
    'google_cloud_run_v2_service':       ('Cloud_Run', 'cloud-run'),
    'google_sql_database_instance':      ('Cloud_SQL', 'cloud-sql'),
    'google_sql_database':               ('Cloud_SQL', 'cloud-sql'),
    'google_bigquery_dataset':           ('BigQuery', 'bigquery'),
    'google_bigquery_table':             ('BigQuery', 'bigquery'),
    'google_container_cluster':          ('GKE', 'kubernetes-engine'),
    'google_container_node_pool':        ('GKE', 'kubernetes-engine'),
    'google_spanner_instance':           ('Cloud_Spanner', 'cloudspanner'),
    'google_firestore_document':         ('Cloud_Storage', 'cloud-storage'),
    'google_kms_key_ring':               ('Cloud_SQL', 'cloud-sql'),
    'google_kms_crypto_key':             ('Cloud_SQL', 'cloud-sql'),
}

@lru_cache(maxsize=512)
def _find_icon_file(category: str, icon_name: str, provider: str = 'azure') -> Optional[Path]:
    """Find the best matching icon file in the filesystem.
    
    Searches for icon files matching the provided category and icon_name.
    Returns the first matching file, or None if not found.
    
    Provider structures:
    - Azure: flat category folders with SVG files (18×18px)
    - AWS: category/64/ with SVG files (80×80px, optimized for Mermaid)
    - GCP: category/SVG/ with SVG files (512×512px viewBox)
    """
    category_path = ICONS_ROOT / provider / category
    if not category_path.exists():
        return None
    
    # Provider-specific path adjustments
    if provider.lower() == 'aws':
        # AWS only has 64px now (16/32/48 deleted for optimization)
        if (category_path / '64').exists():
            category_path = category_path / '64'
    elif provider.lower() == 'gcp':
        # GCP uses SVG subdirectory
        if (category_path / 'SVG').exists():
            category_path = category_path / 'SVG'
        elif (category_path / 'PNG').exists():
            category_path = category_path / 'PNG'
    elif provider.lower() == 'azure':
        # Azure: prefer PNG files from 'png/' subdirectory if available
        png_subdir = category_path / 'png'
        if png_subdir.exists():
            png_files_in_subdir = sorted(png_subdir.rglob("*.png"))
            if png_files_in_subdir:
                # Check for direct match in png subdirectory first
                icon_name_lower = icon_name.lower()
                for icon_file in png_files_in_subdir:
                    if icon_file.stem.lower() == icon_name_lower:
                        return icon_file
    
    # Collect icon files - prioritize PNG over SVG
    icon_name_lower = icon_name.lower()
    png_files = sorted(category_path.glob("*.png"))
    svg_files = sorted(category_path.glob("*.svg"))
    all_files = png_files + svg_files
    
    if not all_files:
        return None
    
    # First pass: exact match (case-insensitive) - PNG preferred
    for icon_file in png_files:
        if icon_file.stem.lower() == icon_name_lower:
            return icon_file
    for icon_file in svg_files:
        if icon_file.stem.lower() == icon_name_lower:
            return icon_file
    
    # Second pass: find files containing all words from icon_name - PNG preferred
    png_word_matches = []
    svg_word_matches = []
    for icon_file in png_files:
        filename_lower = icon_file.stem.lower()
        words = [w for w in icon_name_lower.split('-') if w]
        if all(word in filename_lower for word in words):
            png_word_matches.append(icon_file)
    
    for icon_file in svg_files:
        filename_lower = icon_file.stem.lower()
        words = [w for w in icon_name_lower.split('-') if w]
        if all(word in filename_lower for word in words):
            svg_word_matches.append(icon_file)
    
    if png_word_matches:
        return png_word_matches[0]
    if svg_word_matches:
        return svg_word_matches[0]
    
    # Fallback: return first file in category (PNG preferred)
    return png_files[0] if png_files else (svg_files[0] if svg_files else None)


@lru_cache(maxsize=512)
def _discover_icon_by_name(icon_name: str, provider: str) -> Optional[Path]:
    """Smart icon discovery: search across all categories for matching icon.
    
    Used as fallback when curated mapping doesn't exist.
    Searches all categories for a matching icon file (PNG preferred over SVG).
    
    Examples:
        _discover_icon_by_name('dynamodb', 'aws') → finds aws/Arch_*/dynamodb.png or .svg
        _discover_icon_by_name('storage-account', 'azure') → finds azure/*/storage-account.png or .svg
    """
    provider_path = ICONS_ROOT / provider
    if not provider_path.exists():
        return None
    
    icon_name_lower = icon_name.lower()
    
    # Search PNG files first (preferred format)
    for icon_file in sorted(provider_path.rglob('*.png')):
        if icon_file.stem.lower() == icon_name_lower:
            return icon_file
    
    # Search SVG files as fallback
    for icon_file in sorted(provider_path.rglob('*.svg')):
        if icon_file.stem.lower() == icon_name_lower:
            return icon_file
    
    # Fallback: partial match (PNG files first)
    for icon_file in sorted(provider_path.rglob('*.png')):
        if icon_name_lower in icon_file.stem.lower():
            return icon_file
    
    for icon_file in sorted(provider_path.rglob('*.svg')):
        if icon_name_lower in icon_file.stem.lower():
            return icon_file
    
    return None


def get_icon_path(resource_type: str, provider: str = 'azure') -> Optional[Path]:
    """Get the filesystem path to the icon for a given resource type.
    
    Uses curated mappings first, then falls back to smart discovery.
    
    Args:
        resource_type: Terraform resource type (e.g., 'azurerm_app_service')
        provider: Cloud provider ('azure', 'aws', 'gcp')
    
    Returns:
        Path to the icon SVG file, or None if not found.
    """
    # Normalize resource type
    rtype = (resource_type or '').lower().strip()
    if not rtype:
        return None

    # Check OTHER_RESOURCE_TYPE_TO_ICON first (alicloud, oci, synthetic, etc.)
    if rtype in OTHER_RESOURCE_TYPE_TO_ICON:
        prov_name, icon_rel = OTHER_RESOURCE_TYPE_TO_ICON[rtype]
        parts = icon_rel.split('/', 1)
        if len(parts) == 2:
            cat, name = parts
            return _find_icon_file(cat, name, prov_name)
        return None

    # Select the mapping based on provider
    if provider.lower() == 'azure':
        mapping = AZURE_RESOURCE_TYPE_TO_ICON
    elif provider.lower() == 'aws':
        mapping = AWS_RESOURCE_TYPE_TO_ICON
    elif provider.lower() == 'gcp':
        mapping = GCP_RESOURCE_TYPE_TO_ICON
    elif provider.lower() in ('kubernetes', 'k8s'):
        mapping = KUBERNETES_RESOURCE_TYPE_TO_ICON
        if rtype in mapping:
            category, icon_name = mapping[rtype]
            # Kubernetes icons live in icons/kubernetes/
            icon_file = ICONS_ROOT / category / f"{icon_name}.svg"
            if icon_file.exists():
                return icon_file
        return None
    else:
        # Auto-detect provider from resource type prefix
        if rtype.startswith('azurerm_'):
            mapping = AZURE_RESOURCE_TYPE_TO_ICON
        elif rtype.startswith('aws_'):
            mapping = AWS_RESOURCE_TYPE_TO_ICON
        elif rtype.startswith('google_'):
            mapping = GCP_RESOURCE_TYPE_TO_ICON
        elif rtype.startswith('kubernetes_'):
            mapping = KUBERNETES_RESOURCE_TYPE_TO_ICON
            if rtype in mapping:
                category, icon_name = mapping[rtype]
                icon_file = ICONS_ROOT / category / f"{icon_name}.svg"
                if icon_file.exists():
                    return icon_file
            return None
        elif rtype in OTHER_RESOURCE_TYPE_TO_ICON:
            prov_name, icon_rel = OTHER_RESOURCE_TYPE_TO_ICON[rtype]
            # icon_rel is 'category/icon-name', prov_name is the fallback provider
            parts = icon_rel.split('/', 1)
            if len(parts) == 2:
                cat, name = parts
                return _find_icon_file(cat, name, prov_name)
            return None
        else:
            return None
    
    # Try curated mapping first
    if rtype in mapping:
        category, icon_name = mapping[rtype]
        icon_file = _find_icon_file(category, icon_name, provider)
        if icon_file:
            return icon_file
    
    # Smart fallback: extract service name from resource type
    # e.g., 'azurerm_virtual_machine' → 'virtual_machine' → 'virtual-machine'
    service_name = rtype.replace(f'{provider}_', '').replace('_', '-')
    discovered = _discover_icon_by_name(service_name, provider.lower())
    if discovered:
        return discovered
    
    # Last resort: try shorter service name (first part only)
    # e.g., 'azurerm_app_service_plan' → 'app-service'
    parts = service_name.split('-')
    if len(parts) > 1:
        short_name = '-'.join(parts[:2])
        discovered = _discover_icon_by_name(short_name, provider.lower())
        if discovered:
            return discovered
    
    return None


@lru_cache(maxsize=512)
def get_icon_data_uri(resource_type: str, provider: str = 'azure') -> Optional[str]:
    """Get a data URI for an icon (base64 encoded SVG or PNG).
    
    Args:
        resource_type: Terraform resource type
        provider: Cloud provider
    
    Returns:
        Data URI string (data:image/svg+xml;base64,... or data:image/png;base64,...) or None if not found.
    """
    icon_path = get_icon_path(resource_type, provider)
    if not icon_path or not icon_path.exists():
        return None
    
    try:
        # Read the icon file
        icon_content = icon_path.read_bytes()
        
        # Encode to base64
        b64_encoded = base64.b64encode(icon_content).decode('ascii')
        
        # Determine MIME type based on file extension
        suffix = icon_path.suffix.lower()
        if suffix == '.svg':
            mime_type = 'image/svg+xml'
        elif suffix == '.png':
            mime_type = 'image/png'
        elif suffix == '.jpg' or suffix == '.jpeg':
            mime_type = 'image/jpeg'
        else:
            mime_type = 'image/svg+xml'  # Default to SVG
        
        # Return as data URI
        return f"data:{mime_type};base64,{b64_encoded}"
    except Exception as e:
        print(f"Warning: Failed to read icon file {icon_path}: {e}")
        return None


def get_fallback_icon_data_uri(provider: str = 'azure') -> Optional[str]:
    """Get a generic fallback icon when no specific icon is found.
    
    Args:
        provider: Cloud provider
    
    Returns:
        Data URI for a generic/resource icon.
    """
    # Try to find a generic/resource icon in the general category
    icon_path = _find_icon_file('general', 'resource', provider)
    if icon_path and icon_path.exists():
        try:
            svg_content = icon_path.read_bytes()
            b64_encoded = base64.b64encode(svg_content).decode('ascii')
            return f"data:image/svg+xml;base64,{b64_encoded}"
        except Exception:
            pass
    
    return None


def build_icon_map_bulk(provider: str = 'azure') -> dict:
    """Build a complete icon map for a provider by walking the filesystem once.
    
    This is much faster than calling get_icon_path() for each resource individually,
    since it walks the directory tree once instead of repeatedly.
    
    Args:
        provider: Cloud provider ('azure', 'aws', 'gcp', 'kubernetes', 'other')
    
    Returns:
        Dict mapping resource_type -> icon_url (e.g., '/static/assets/icons/azure/web/png/app-service/app-service.png')
    """
    icon_map = {}
    provider_root = ICONS_ROOT / provider
    
    if not provider_root.exists():
        return icon_map
    
    # Get the mapping dict for this provider
    if provider == 'azure':
        resource_mapping = AZURE_RESOURCE_TYPE_TO_ICON
    elif provider == 'aws':
        resource_mapping = AWS_RESOURCE_TYPE_TO_ICON
    elif provider == 'gcp':
        resource_mapping = GCP_RESOURCE_TYPE_TO_ICON
    elif provider == 'kubernetes':
        resource_mapping = KUBERNETES_RESOURCE_TYPE_TO_ICON
    else:
        resource_mapping = OTHER_RESOURCE_TYPE_TO_ICON
    
    # Walk the provider's icon directory once
    icon_files_by_name = {}  # {icon_name_lower: [list of Path objects]}
    
    for icon_file in provider_root.rglob("*"):
        if icon_file.is_file() and icon_file.suffix.lower() in ('.png', '.svg'):
            # Store by filename (without extension)
            name_lower = icon_file.stem.lower()
            if name_lower not in icon_files_by_name:
                icon_files_by_name[name_lower] = []
            icon_files_by_name[name_lower].append(icon_file)
    
    # Sort files so PNG is preferred over SVG
    for name in icon_files_by_name:
        icon_files_by_name[name].sort(key=lambda p: (p.suffix.lower() != '.png', str(p)))
    
    # Map each resource type to its best icon file
    # Web root is the directory containing 'static' (i.e., /repo/web)
    web_root = ICONS_ROOT.parent.parent.parent
    
    for resource_type, (category, icon_name) in resource_mapping.items():
        icon_name_lower = icon_name.lower()
        
        # Try exact match first
        if icon_name_lower in icon_files_by_name:
            icon_file = icon_files_by_name[icon_name_lower][0]
            try:
                rel_path = icon_file.relative_to(web_root)
                icon_url = f"/{rel_path.as_posix()}"
                icon_map[resource_type] = icon_url
                continue
            except Exception:
                pass
        
        # Try word-based match
        words = [w for w in icon_name_lower.split('-') if w]
        best_match = None
        for name, files in icon_files_by_name.items():
            if all(word in name for word in words):
                best_match = files[0]  # Already sorted (PNG first)
                break
        
        if best_match:
            try:
                rel_path = best_match.relative_to(web_root)
                icon_url = f"/{rel_path.as_posix()}"
                icon_map[resource_type] = icon_url
            except Exception:
                pass
    
    return icon_map


# Kubernetes resource type to icon mappings
# Icons are stored in web/static/assets/icons/kubernetes/
KUBERNETES_RESOURCE_TYPE_TO_ICON = {
    'kubernetes_deployment': ('kubernetes', 'deployment'),
    'kubernetes_daemonset': ('kubernetes', 'daemonset'),
    'kubernetes_statefulset': ('kubernetes', 'statefulset'),
    'kubernetes_service': ('kubernetes', 'service'),
    'kubernetes_pod': ('kubernetes', 'pod'),
    'kubernetes_namespace': ('kubernetes', 'namespace'),
    'kubernetes_ingress': ('kubernetes', 'ingress'),
    'kubernetes_job': ('kubernetes', 'job'),
    'kubernetes_cronjob': ('kubernetes', 'cronjob'),
    'kubernetes_configmap': ('kubernetes', 'configmap'),
    'kubernetes_secret': ('kubernetes', 'secret'),
    'kubernetes_cluster': ('kubernetes', 'cluster'),
    'kubernetes_replicaset': ('kubernetes', 'deployment'),
    'kubernetes_networkpolicy': ('kubernetes', 'ingress'),
    'kubernetes_serviceaccount': ('kubernetes', 'service'),
}

# Alicloud, OCI, and synthetic resource types — use provider-agnostic fallback icons
# These live in the Azure/GCP icon directories which are already served under /static
OTHER_RESOURCE_TYPE_TO_ICON: dict = {
    # Alicloud
    'alicloud_db_instance':       ('azure', 'databases/sql-database'),
    'alicloud_oss_bucket':        ('azure', 'storage/storage-account'),
    'alicloud_instance':          ('azure', 'compute/virtual-machine'),
    'alicloud_vpc':               ('azure', 'networking/virtual-networks'),
    'alicloud_vswitch':           ('azure', 'networking/virtual-networks'),
    'alicloud_security_group':    ('azure', 'networking/nsg'),
    'alicloud_ram_role':          ('azure', 'identity/managed-identities'),
    'alicloud_ram_policy':        ('azure', 'security/microsoft-defender-for-cloud'),
    # Oracle Cloud Infrastructure
    'oci_objectstorage_bucket':   ('azure', 'storage/storage-account'),
    'oci_core_instance':          ('azure', 'compute/virtual-machine'),
    'oci_core_vcn':               ('azure', 'networking/virtual-networks'),
    'oci_core_subnet':            ('azure', 'networking/virtual-networks'),
    'oci_database_db_system':     ('azure', 'databases/sql-database'),
    # Synthetic/inferred nodes (created by diagram generator)
    'synthetic_sql_server':       ('azure', 'databases/sql-database'),
    'synthetic_database':         ('azure', 'databases/sql-database'),
    'synthetic_storage':          ('azure', 'storage/storage-accounts'),
    'synthetic_server':           ('azure', 'compute/virtual-machine'),
}


def get_icon_class(resource_type: str, provider: str = 'azure') -> str:
    """Get a CSS class name for a resource type.

    The class name is derived solely from the resource type, which already
    encodes the provider prefix (e.g. ``aws_``, ``azurerm_``, ``google_``).
    Prepending the provider again would double the prefix and break the
    lookup in ``mermaid-icon-injector.js``.

    Args:
        resource_type: Terraform/Kubernetes resource type
        provider: Cloud provider (kept for API compatibility, not used in name)

    Returns:
        CSS class name (e.g. 'icon-aws-security-group',
        'icon-azurerm-app-service', 'icon-google-compute-instance')
    """
    rtype = (resource_type or '').lower().replace('_', '-')
    return f"icon-{rtype}"


def build_icon_css(resources: list, provider: str = 'azure') -> str:
    """Build a CSS stylesheet for icon styling.
    
    Args:
        resources: List of resource dicts (each with 'resource_type' field)
        provider: Cloud provider
    
    Returns:
        CSS stylesheet as a string.
    """
    lines = [
        "/* Icon styling for cloud architecture diagrams */",
        "",
        "/* Base icon styling */",
        ".mermaid g.nodes g g text { font-size: 12px; }",
        "",
        "/* Icon nodes with background images */",
    ]
    
    # Collect unique resource types
    resource_types = set()
    for res in resources:
        if isinstance(res, dict):
            rtype = res.get('resource_type', '')
            if rtype:
                resource_types.add(rtype)
    
    # Generate CSS for each resource type
    for rtype in sorted(resource_types):
        icon_uri = get_icon_data_uri(rtype, provider)
        if not icon_uri:
            # Use fallback icon
            icon_uri = get_fallback_icon_data_uri(provider)
        
        if icon_uri:
            css_class = get_icon_class(rtype, provider)
            lines.append(f".{css_class} {{ background-image: url('{icon_uri}'); }}")
    
    # Generic icon styling
    lines.extend([
        "",
        "/* Generic icon node styling */",
        ".icon-node {",
        "  background-size: contain;",
        "  background-repeat: no-repeat;",
        "  background-position: center;",
        "  padding: 16px;",
        "  min-width: 64px;",
        "  min-height: 64px;",
        "  display: flex;",
        "  align-items: center;",
        "  justify-content: center;",
        "}",
    ])
    
    return "\n".join(lines)


def get_all_available_icons(provider: str = 'azure') -> Dict[str, Set[str]]:
    """Get a dictionary of all available icons organized by category.
    
    Args:
        provider: Cloud provider
    
    Returns:
        Dict mapping category name to set of icon file paths.
    """
    provider_icons_root = ICONS_ROOT / provider
    if not provider_icons_root.exists():
        return {}
    
    result = {}
    for category_dir in provider_icons_root.iterdir():
        if category_dir.is_dir():
            svg_files = set(category_dir.glob("*.svg"))
            if svg_files:
                result[category_dir.name] = svg_files
    
    return result


if __name__ == "__main__":
    # Test the icon resolver
    print("Testing icon resolver...")
    
    # Test Azure icons
    test_types = [
        'azurerm_app_service',
        'azurerm_sql_database',
        'azurerm_key_vault',
        'azurerm_kubernetes_cluster',
        'azurerm_virtual_network',
    ]
    
    for rtype in test_types:
        icon_path = get_icon_path(rtype)
        icon_uri = get_icon_data_uri(rtype)
        print(f"{rtype}:")
        print(f"  Path: {icon_path}")
        print(f"  URI length: {len(icon_uri) if icon_uri else 'N/A'}")
    
    # Test CSS generation
    resources = [{'resource_type': rt} for rt in test_types]
    css = build_icon_css(resources)
    print(f"\nGenerated CSS ({len(css)} chars):")
    print(css[:500] + "...")
