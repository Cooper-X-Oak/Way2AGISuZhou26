"""Compatibility entry point for the explicit L4 compliance demo."""
import sys
from pipeline.__main__ import main

if __name__ == '__main__':
    sys.argv = ['pipeline', 'run', '--offline', '--auto-approve', '--demo-replay', 'compliance', '--profile', 'jiangnan-gongquan', '--input', '我是苏州高新区的经销商，想把江南贡泉铺进周边3公里的饭店，手下2个地推，做4周']
    raise SystemExit(main())
