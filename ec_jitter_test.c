/* ec_jitter_test — T153 ethercat 性能: 1kHz cyclic WKC 完整性 + 周期定时抖动
 *
 * 基于 ec_wkc_test v4 (HPM5E31 ecat_uart port1, 期望 wc=3), 增加:
 *   wake dev = now - scheduled (clock_nanosleep TIMER_ABSTIME, SCHED_FIFO)
 *   work     = receive..send 每周期处理耗时
 * 板 RAM 91MB → 无 per-cycle 存储, 只在线统计 + 直方图.
 *
 * 编译 (linaro 11.3.1, $P = 1.7 userspace install 目录):
 *   arm-linux-gnueabihf-gcc -O2 ec_jitter_test.c -o ec_jitter_test \
 *       -I$P/include -L$P/lib -lethercat -lpthread -lrt
 * 运行: sudo /home/mika/ec_jitter_test [秒数, 默认 60]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <limits.h>
#include <errno.h>
#include <sched.h>
#include <time.h>
#include <sys/mman.h>
#include <ecrt.h>

#define SECONDS_DEFAULT 60
#define PERIOD_NS (1000000000UL / 1000UL)
#define EXPECTED_WKC 3

#define VENDOR_ID    0x00000A0Cu
#define PRODUCT_CODE 0x00000001u
#define PORT_N 1
#define TX_BASE (0x6000u + (PORT_N) * 0x10u)
#define RX_BASE (0x7000u + (PORT_N) * 0x10u)

static ec_master_t *master;
static ec_domain_t *dom;

static unsigned int off_status, off_rxlen, off_rxdata;
static unsigned int off_control, off_txlen, off_txdata;

static const ec_pdo_entry_reg_t regs[] = {
    {0, 0, VENDOR_ID, PRODUCT_CODE, TX_BASE + 0, 0x00, &off_status,  NULL},
    {0, 0, VENDOR_ID, PRODUCT_CODE, TX_BASE + 1, 0x00, &off_rxlen,   NULL},
    {0, 0, VENDOR_ID, PRODUCT_CODE, TX_BASE + 2, 0x01, &off_rxdata,  NULL},
    {0, 0, VENDOR_ID, PRODUCT_CODE, RX_BASE + 0, 0x00, &off_control, NULL},
    {0, 0, VENDOR_ID, PRODUCT_CODE, RX_BASE + 1, 0x00, &off_txlen,   NULL},
    {0, 0, VENDOR_ID, PRODUCT_CODE, RX_BASE + 2, 0x01, &off_txdata,  NULL},
    {}
};

static void nsec_diff(const struct timespec *a, const struct timespec *b,
                      int64_t *out)
{
    *out = (int64_t)(a->tv_sec - b->tv_sec) * 1000000000LL
         + (a->tv_nsec - b->tv_nsec);
}

/* wake-dev 直方图: [early] 0-100us 100-200us 200-300us 300-500us 0.5-1ms 1-2ms 2-5ms >5ms */
#define NB_DEV 9
static const int64_t dev_edge[NB_DEV - 1] = {
    100000, 200000, 300000, 500000, 1000000, 2000000, 5000000
};
static const char *dev_label[NB_DEV] = {
    "<0(early)", "0-100us", "100-200us", "200-300us", "300-500us",
    "0.5-1ms", "1-2ms", "2-5ms", ">5ms"
};

/* work 直方图: <50us 50-100us 100-250us 250-500us >500us */
#define NB_WORK 5
static const int64_t work_edge[NB_WORK - 1] = { 50000, 100000, 250000, 500000 };
static const char *work_label[NB_WORK] = {
    "<50us", "50-100us", "100-250us", "250-500us", ">500us"
};

int main(int argc, char **argv)
{
    int seconds = argc > 1 ? atoi(argv[1]) : SECONDS_DEFAULT;
    ec_slave_config_t *sc;
    ec_slave_config_state_t scs;
    ec_domain_state_t ds;
    uint8_t *pd;
    struct timespec next;
    uint64_t cycles = 0, wc_bad = 0, wc_zero = 0;
    uint16_t last_wc = 0;
    int64_t t_end = (int64_t)seconds * 1000000000LL / (int64_t)PERIOD_NS;

    uint64_t dev_hist[NB_DEV] = {0}, work_hist[NB_WORK] = {0};
    int64_t dev_min = INT64_MAX, dev_max = INT64_MIN, dev_sum = 0;
    int64_t work_min = INT64_MAX, work_max = INT64_MIN, work_sum = 0;
    int i;

    master = ecrt_request_master(0);
    if (!master) { fprintf(stderr, "request_master 失败\n"); return 1; }
    dom = ecrt_master_create_domain(master);
    if (!dom) { fprintf(stderr, "create_domain 失败\n"); return 1; }

    sc = ecrt_master_slave_config(master, 0, 0, VENDOR_ID, PRODUCT_CODE);
    if (!sc) { fprintf(stderr, "slave_config 失败 (从站不在总线?)\n"); return 1; }

    if (ecrt_domain_reg_pdo_entry_list(dom, regs)) {
        fprintf(stderr, "domain reg 失败\n");
        return 1;
    }

    if (ecrt_master_activate(master)) {
        fprintf(stderr, "activate 失败\n");
        return 1;
    }
    pd = ecrt_domain_data(dom);
    if (!pd) { fprintf(stderr, "domain_data 失败\n"); return 1; }

    printf("=== ec_jitter_test: %d s @ 1kHz, ecat_uart port%d, 期望 wc=%d ===\n",
           seconds, PORT_N, EXPECTED_WKC);
    printf("off: status=%u rxlen=%u rxdata=%u control=%u txlen=%u txdata=%u\n",
           off_status, off_rxlen, off_rxdata, off_control, off_txlen, off_txdata);
    fflush(stdout);

    {
        struct sched_param sp = { .sched_priority = sched_get_priority_max(SCHED_FIFO) / 2 };
        if (sched_setscheduler(0, SCHED_FIFO, &sp) == 0)
            printf("SCHED_FIFO prio=%d\n", sp.sched_priority);
        else
            printf("WARN: SCHED_FIFO 失败 (errno=%d)\n", errno);
    }
    mlockall(MCL_CURRENT | MCL_FUTURE);

    EC_WRITE_U16(pd + off_control, 0);

    clock_gettime(CLOCK_MONOTONIC, &next);
    next.tv_sec += 1;

    if (t_end <= 0) { fprintf(stderr, "bad t_end\n"); return 3; }

    /* phase 1 (grace): 泵到 OP. 10s 预算, 每 10 周期查一次 state */
    {
        uint64_t g;
        for (g = 0; g < 10000; g++) {
            clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
            next.tv_nsec += PERIOD_NS;
            while (next.tv_nsec >= 1000000000L) {
                next.tv_nsec -= 1000000000L;
                next.tv_sec++;
            }
            ecrt_master_receive(master);
            ecrt_domain_process(dom);
            EC_WRITE_U16(pd + off_control, 0);
            ecrt_domain_queue(dom);
            ecrt_master_send(master);
            if ((g % 10) == 0) {
                ecrt_slave_config_state(sc, &scs);
                if (scs.operational) {
                    printf("grace: OP after %ums\n", (unsigned)(g / 10));
                    break;
                }
            }
        }
        ecrt_slave_config_state(sc, &scs);
        if (!scs.operational) {
            printf("FAIL: no OP in grace (10s) — al=%u err=%u\n",
                   scs.al_state, scs.error_flag);
            return 4;
        }
    }
    fflush(stdout);

    /* phase 2 (count): OP 后满 seconds — WKC 完整性 + 定时统计 */
    cycles = 0;
    while (cycles < t_end) {
        struct timespec sched = next, now, t0, t1;
        int64_t dev, work;

        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
        next.tv_nsec += PERIOD_NS;
        while (next.tv_nsec >= 1000000000L) {
            next.tv_nsec -= 1000000000L;
            next.tv_sec++;
        }

        clock_gettime(CLOCK_MONOTONIC, &now);
        nsec_diff(&now, &sched, &dev);
        if (dev < 0)
            dev_hist[0]++;
        else {
            for (i = 0; i < NB_DEV - 1; i++)
                if (dev < dev_edge[i]) { dev_hist[i + 1]++; goto dev_done; }
            dev_hist[NB_DEV - 1]++;
        }
        dev_done:
        ;
        if (dev < dev_min) dev_min = dev;
        if (dev > dev_max) dev_max = dev;
        dev_sum += dev;

        ecrt_master_receive(master);
        ecrt_domain_process(dom);
        clock_gettime(CLOCK_MONOTONIC, &t0);

        ecrt_domain_state(dom, &ds);
        if (ds.wc_state != EC_WC_COMPLETE) {
            wc_bad++;
            if (ds.wc_state == EC_WC_ZERO)
                wc_zero++;
            last_wc = ds.working_counter;
        }

        if ((cycles % 1000) == 0) {
            ecrt_slave_config_state(sc, &scs);
            uint16_t st = EC_READ_U16(pd + off_status);
            printf("t=%5llds wc=%u/%d slv online=%u al=%u op=%u err=%u st=0x%04x\n",
                   (long long)(cycles / 1000), ds.working_counter,
                   EXPECTED_WKC, scs.online, scs.al_state,
                   scs.operational, scs.error_flag, st);
            fflush(stdout);
        }

        EC_WRITE_U16(pd + off_control, 0);
        ecrt_domain_queue(dom);
        ecrt_master_send(master);
        clock_gettime(CLOCK_MONOTONIC, &t1);
        nsec_diff(&t1, &t0, &work);

        for (i = 0; i < NB_WORK - 1; i++)
            if (work < work_edge[i]) { work_hist[i + 1]++; goto work_done; }
        work_hist[NB_WORK - 1]++;
        work_done:
        ;
        if (work < work_min) work_min = work;
        if (work > work_max) work_max = work;
        work_sum += work;

        cycles++;
    }

    ecrt_slave_config_state(sc, &scs);
    printf("=== RESULT ===\n");
    printf("cycles=%llu  wc_bad=%llu (zero=%llu, last_wc=%u)\n",
           (unsigned long long)cycles, (unsigned long long)wc_bad,
           (unsigned long long)wc_zero, last_wc);
    printf("final: online=%u al=%u op=%u err=%u\n",
           scs.online, scs.al_state, scs.operational, scs.error_flag);

    printf("=== JITTER (wake, ns) ===\n");
    printf("min=%lld max=%lld mean=%lld\n",
           (long long)dev_min, (long long)dev_max,
           (long long)(dev_sum / (int64_t)cycles));
    for (i = 0; i < NB_DEV; i++)
        printf("  %-10s %llu  (%.2f%%)\n", dev_label[i],
               (unsigned long long)dev_hist[i],
               100.0 * (double)dev_hist[i] / (double)cycles);

    printf("=== WORK (recv..send, ns) ===\n");
    printf("min=%lld max=%lld mean=%lld\n",
           (long long)work_min, (long long)work_max,
           (long long)(work_sum / (int64_t)cycles));
    for (i = 0; i < NB_WORK; i++)
        printf("  %-10s %llu  (%.2f%%)\n", work_label[i],
               (unsigned long long)work_hist[i],
               100.0 * (double)work_hist[i] / (double)cycles);

    if (wc_bad == 0 && scs.operational)
        printf("PASS: 全周期 WKC COMPLETE + OP\n");
    else if (wc_bad == 0)
        printf("FAIL: WKC 全齐但从站未 operational (al=%u)\n", scs.al_state);
    else
        printf("FAIL: %llu/%llu 周期 WKC 不完整\n",
               (unsigned long long)wc_bad, (unsigned long long)cycles);
    return (wc_bad == 0 && scs.operational) ? 0 : 2;
}
