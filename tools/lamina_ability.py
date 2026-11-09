"""Ability picker for the isolated experimental client.

The UI queues requests; only the debugger's existing profile owner commits them.
Requests are consumed at menu/loadout events, never by a per-frame combat hook.
"""
import json
import os
from pathlib import Path
import uuid
import textwrap


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def request_path(store):
    return Path(store).with_name('lamina_ability_request.json')


def status_path(store):
    return Path(store).with_name('lamina_ability_status.json')


def queue(store, lamina_id, variant):
    if type(lamina_id) is not int or not 1 <= lamina_id <= 15:
        raise ValueError('Invalid Lamina')
    if type(variant) is not int or not 0 <= variant <= 2:
        raise ValueError('Invalid ability')
    value = dict(request_id=uuid.uuid4().hex, lamina_id=lamina_id, variant=variant)
    atomic_json(request_path(store), value)
    return value


def consume(store, module, profile_store):
    pending = request_path(store)
    processing = pending.with_name('lamina_ability_processing.json')
    try:
        pending.replace(processing)
    except FileNotFoundError:
        if not processing.exists():
            return None
    value = {}
    try:
        if processing.stat().st_size > 2048:
            raise ValueError('Ability request is too large')
        value = json.loads(processing.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or set(value) != {'request_id', 'lamina_id', 'variant'}:
            raise ValueError('Invalid ability request')
        ident = value['request_id']
        if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('Invalid request identity')
        lamina_id, variant = value['lamina_id'], value['variant']
        if type(lamina_id) is not int or not 1 <= lamina_id <= 15:
            raise ValueError('Invalid Lamina')
        if type(variant) is not int or not 0 <= variant <= 2:
            raise ValueError('Invalid ability')
        with profile_store.LOCK:
            current = module.state(profile_store.read(store))
            if current['selected_id'] != lamina_id:
                raise ValueError('Selected Lamina changed; choose again for the current Lamina')
            # select enforces the existing unlock policy and preserves XP/color/events.
            module.select(store, lamina_id, variant)
        result = dict(request_id=ident, status='saved', lamina_id=lamina_id, variant=variant)
    except Exception as exc:
        result = dict(request_id=value.get('request_id') if isinstance(value, dict) else None,
                      status='rejected', error=str(exc))
    atomic_json(status_path(store), result)
    processing.unlink(missing_ok=True)
    return result


def gui():
    import sys
    import tkinter as tk
    from tkinter import ttk
    here = Path(__file__).resolve().parent
    if not (here/'RELEASE-CLIENT').is_file():
        raise RuntimeError('Use the independent experimental installation')
    sys.path.insert(0, str(here))
    import profile_store
    import roi_lamina
    store = here / 'lamina_profile.json'
    descriptions = json.loads((here / 'lamina_abilities.json').read_text(encoding='utf-8'))
    root = tk.Tk()
    root.title('Lamina Abilities — Experimental')
    root.geometry('740x620')
    root.minsize(720, 610)
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill='both', expand=True)
    heading = tk.StringVar()
    active = tk.StringVar()
    status = tk.StringVar(value='Choose one ability for your current Lamina.')
    picked = tk.IntVar(value=0)
    ttk.Label(frame, textvariable=heading, font=('Segoe UI', 19, 'bold')).pack(anchor='w')
    ttk.Label(frame, textvariable=active, padding=(0, 8)).pack(anchor='w')
    ttk.Label(frame, text='Active ability', font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(12, 4))
    buttons = []
    for i in range(3):
        radio = ttk.Radiobutton(frame, variable=picked, value=i, text='', padding=(8, 13))
        radio.pack(anchor='w', fill='x')
        buttons.append(radio)
    ttk.Label(frame, text='Applies to the next battle. After saving, open Profile → Lamina Select\n'
              'or start a new battle to load your choice.', wraplength=660).pack(anchor='w', pady=(18, 8))
    ttk.Label(frame, text='Experimental client: all three variants are available. Original level unlocks\n'
              'are not yet restored; your XP and progression settings are preserved.',
              wraplength=660, foreground='#555555').pack(anchor='w', pady=(0, 12))
    current_id = None
    submitted = None

    def refresh():
        nonlocal current_id, submitted
        try:
            value = roi_lamina.state(profile_store.read(store))
            selected = value['selected_id']
            variant = value['laminas'][str(selected)]['variant']
            heading.set(roi_lamina.LAMINA_NAMES[selected - 1])
            active.set(f'Currently saved: Ability {variant + 1}')
            if selected != current_id:
                current_id = selected
                picked.set(variant)
                submitted = None
                status.set('Choose one ability for your current Lamina.')
            for i, radio in enumerate(buttons):
                radio.configure(text=textwrap.fill(f'{i + 1}. {descriptions[str(selected)][i]}', width=85),
                                state='normal' if roi_lamina._unlocked(value, selected, i) else 'disabled')
            if submitted and status_path(store).exists():
                result = json.loads(status_path(store).read_text(encoding='utf-8'))
                if result.get('request_id') == submitted:
                    status.set(f"Saved: Ability {result['variant'] + 1}. Ready for the next battle."
                               if result['status'] == 'saved' else result.get('error', 'Request rejected'))
        except Exception as exc:
            status.set(str(exc))
        root.after(1000, refresh)

    def apply():
        nonlocal submitted
        try:
            value = roi_lamina.state(profile_store.read(store))
            if value['selected_id'] != current_id:
                raise ValueError('Selected Lamina changed; wait for the picker to refresh')
            if not roi_lamina._unlocked(value, current_id, picked.get()):
                raise ValueError('This ability is locked by the current progression policy')
            submitted = queue(store, current_id, picked.get())['request_id']
            status.set('Queued. Open Lamina Select or start the next battle to apply.')
        except Exception as exc:
            status.set(str(exc))

    ttk.Button(frame, text='Use selected ability', command=apply).pack(anchor='w')
    ttk.Label(frame, textvariable=status, wraplength=660, padding=(0, 12)).pack(anchor='w')
    refresh()
    root.mainloop()


if __name__ == '__main__':
    gui()
