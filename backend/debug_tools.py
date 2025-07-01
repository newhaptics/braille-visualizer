import asyncio, threading, traceback, logging

def dump_live_objects():
    # ---- asyncio Tasks ----------------------------------------------
    for task in asyncio.all_tasks():
        if task.done():
            continue
        logging.warning("PENDING TASK: %r", task)
        for frame in task.get_stack():
            logging.warning("⤷ %s",
                            "".join(traceback.format_stack(frame)[-2:]))

    # ---- threads -----------------------------------------------------
    for th in threading.enumerate():
        if th is threading.main_thread():
            continue
        logging.warning("LIVE THREAD: %s (%s)", th.name, th.ident)