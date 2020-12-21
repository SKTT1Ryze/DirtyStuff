import sh
import os
import re
import os.path as osp
import sys, getopt
from pprint import pprint
import multiprocessing
from multiprocessing import Pool, Lock, Manager, Process

from common.simulator_task_goback import SimulatorTaskGoBack
from common.task_tree_go_back import task_tree_to_batch_task
from emutasks.config import EmuTasksConfig

# 运行： `PYTHONPATH=/path/to/this/project python3 /path/to/this/file [参数]`
# 参数：  
# + -I 仿真最大指令数
# + -e 仿真可执行文件的目录（比如 XiangShan/build/emu）
# + -T 仿真的线程数（一般和香山 emu 编译时候指定的线程数相同）
# + -h 帮助列表

# 运行这个脚本前请先设置操作系统允许打开的最大文件数：  
#  ulimit -n 最大文件数，可以设成 4096, 即  `ulimit -n 4096`

# 另外需要在香山项目下的 `src/test/csrc/` 进行相应的修改，让它返回 `trapcode`，像这样：  

# emu.h:  
# class Emulator {
#   ......
# #endif
#   EmuArgs args;

#   enum {
#     STATE_GOODTRAP = 0,
#     STATE_BADTRAP = 1,
#     STATE_ABORT = 2,
#     STATE_LIMIT_EXCEEDED = 3,
#     STATE_RUNNING = -1
#   };

#   ......

# public:
#   ......
#   bool is_good_trap() { return trapCode == STATE_GOODTRAP; };
#   int trap_code() { return trapCode; }
# };

# main.cpp:  
# int main(int argc, const char** argv) {
#   ......
#   int trapcode = emu->trap_code();
#   ......
#   return trapcode;
# }

TaskSummary = {}

# 使用者需要修改以下写死的参数  

exe = 'numactl'
emu = '/home/ccc/XiangShan/build/emu' # 仿真可执行文件的目录
data_dir = '/bigdata/ccc_data/gcpt_shared/random_cpts' # 测试用例目录
top_output_dir = '/home/ccc/DirtyStuff/output/EMU/' # 结果输出目录

cpt_dir_pattern = re.compile(r'\d+')
THREADS_NUM = 8
MAX_CORE = 128
MAX_INSTR = 1000000

def find_task(d: str):
    for workload in os.listdir(d):
        workload_dir = osp.join(d, workload)
        if not osp.isdir(workload_dir):
            continue
        TaskSummary[workload] = {}
        for cpt in os.listdir(workload_dir):
            cpt_dir = osp.join(workload_dir, cpt)
            if not cpt_dir_pattern.match(cpt) or not osp.isdir(cpt_dir):
                continue
            cpt_file = os.listdir(cpt_dir)[0]
            cpt_file = osp.join(cpt_dir, cpt_file)
            assert osp.isfile(cpt_file)

            TaskSummary[workload][cpt] = cpt_file
    return TaskSummary

def task_wrapper(task: SimulatorTaskGoBack, thread_num: int, cores_id: int, cores):
    core_options = [
        '-C',
        str(cores_id * thread_num) + '-' + str(cores_id * thread_num + thread_num - 1)
    ]
    task.insert_direct_options(core_options, 0)
    is_goback = False
    cycle_cnt = task.run(False)
    simulator_success = (cycle_cnt == 0)
    if not simulator_success:
        print('simulator abort, go back...')
        back_cycle_cnt =  cycle_cnt - 10000
        task.add_direct_options(['-b', str(back_cycle_cnt), '-e', '-1', '--dump-wave'])
        cycle_cnt = task.run(True)
        simulator_success = (cycle_cnt == 0)
        is_goback = True
    cores[cores_id] = 0
    print(cores_id * thread_num, cores_id * thread_num + thread_num - 1, "simulator task finish")
    # return simulator_success, is_goback, cycle_cnt, task.workload, task.sub_phase_id
    sys.exit()

if __name__ == "__main__":
    argv = sys.argv[1:]
    try:
        opts, args = getopt.getopt(argv, "he:I:T:")
    except getopt.GetoptError:
        print("py3 this-file -e <Simulator Executable File> -I <Max Instruction> -T <Threads Num>")
        sys.exit(2)
    for opt, arg in opts:
        if opt == '-h':
            print("py3 this-file -e <Simulator Executable File> -I <Max Instruction> -T <Threads Num>")
            sys.exit()
        elif opt == '-e':
            emu = arg
        elif opt == '-I':
            MAX_INSTR = int(arg)
            # assert(MAX_INSTR > 10000)
        elif opt == '-T':
            THREADS_NUM = int(arg)
            assert(THREADS_NUM > 0)
            assert(THREADS_NUM % 4 == 0)

    task_tree = find_task(data_dir)
    # pprint(task_tree)
    tasks = task_tree_to_batch_task(EmuTasksConfig, task_tree, exe, top_output_dir, "emu_ooo_run_sepc06_cpt", emu, MAX_INSTR)

    for task in tasks:
        # task.dry_run = True
        task.sub_workload_level_path_format()
        task.set_trivial_workdir()

        cpt_file = task_tree[task.workload][task.sub_phase_id]
        # print(cpt_file)
        task.direct_options += ['-i', cpt_file]
        task.add_dict_options({
            # TODO
        })

        task.format_options()

    with Manager()  as manager:
        cores_list = [0] * int(MAX_CORE / THREADS_NUM)
        cores = manager.list(cores_list)
        task_id = 0
        proc_list = []
        while True:
            if 0 not in cores:
                continue
            c_id = cores.index(0)
            cores[c_id] = 1
            if task_id >= len(tasks):
                break
            p = Process(target=task_wrapper, args=(tasks[task_id], THREADS_NUM, c_id, cores))
            task_id += 1
            p.start()
            proc_list.append(p)
        for proc in proc_list:
            proc.join()
