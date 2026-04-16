def open_advanced_sub_editor(sub_data=None):
    with ui.dialog() as d: AdvancedSubEditor(sub_data).ui(d); d.open()