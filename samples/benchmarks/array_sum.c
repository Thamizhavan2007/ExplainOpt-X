#include <stdio.h>

int main(void) {
    static int a[10000];
    volatile long long total = 0;

    for (int i = 0; i < 10000; i++)
        a[i] = i % 100;

    for (int repeat = 0; repeat < 20000; repeat++)
        for (int i = 0; i < 10000; i++)
            total += a[i];

    printf("%lld\n", total);
    return 0;
}
