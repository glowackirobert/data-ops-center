import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.concurrent.ThreadLocalRandom;

public class RandomGenerator {

    private static final String CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

    public static String generateStringValue() {
        var sb = new StringBuilder(5);
        for (int i = 0; i < 5; i++) {
            var randomIndex = ThreadLocalRandom.current().nextInt(CHARACTERS.length());
            sb.append(CHARACTERS.charAt(randomIndex));
        }
        return sb.toString();
    }

    public static int generateIntValue() {
        return ThreadLocalRandom.current().nextInt(Integer.MAX_VALUE);
    }

    public static boolean generateBooleanValue() {
        return ThreadLocalRandom.current().nextBoolean();
    }

    public static <T extends Enum<?>> T getRandomEnumValue(Class<T> enumClass) {
        T[] enumValues = enumClass.getEnumConstants();
        int randomIndex = ThreadLocalRandom.current().nextInt(enumValues.length);
        return enumValues[randomIndex];
    }

    public static long generateRandomNanoTimestamp() {
        Instant now = Instant.now();
        Instant oneYearAgo = now.minus(365, ChronoUnit.DAYS);

        long startEpochNano = oneYearAgo.getEpochSecond() * 1_000_000_000L + oneYearAgo.getNano();
        long endEpochNano = now.getEpochSecond() * 1_000_000_000L + now.getNano();

        return ThreadLocalRandom.current().nextLong(startEpochNano, endEpochNano);
    }
}
