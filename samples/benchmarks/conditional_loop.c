#include <stdio.h>
int main(void) {
    static int a[100000];
    for (int i = 0; i < 100000; i++)
        a[i] = i % 17;

    long long sum = 0;
    for (int i = 0; i < 100000; i++) {
        if (a[i] % 2 == 0)
            sum += a[i] * 3;
        else
            sum -= a[i];
    }
    printf("%lld\n", sum);
    return 0;
}
