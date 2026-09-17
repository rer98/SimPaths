package simpaths.model;

import jakarta.persistence.*;
import java.lang.reflect.*;
import java.util.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class SimPathsProcessedResourcesTest {
    private static Object invoke(String name, Class<?>[] types, Object... args) throws Exception {
        Method method = SimPathsModel.class.getDeclaredMethod(name, types);
        method.setAccessible(true);
        return method.invoke(null, args);
    }

    private static EntityManagerFactory factory(String path) throws Exception {
        return (EntityManagerFactory) invoke("getProcessedPopulationFactory", new Class<?>[]{String.class}, path);
    }

    private static void closeFactory() throws Exception {
        invoke("closeProcessedPopulationFactory", new Class<?>[]{});
    }

    @Test void samePathReusesFactoryAndChangedPathClosesBeforeReplacement() throws Exception {
        var first = mock(EntityManagerFactory.class);
        var second = mock(EntityManagerFactory.class);
        when(first.isOpen()).thenReturn(true);
        when(second.isOpen()).thenReturn(true);
        try (var persistence = mockStatic(Persistence.class)) {
            persistence.when(() -> Persistence.createEntityManagerFactory(eq("starting-population"), anyMap()))
                .thenAnswer(call -> {
                    Map<?,?> properties = call.getArgument(1);
                    String url = (String) properties.get("hibernate.connection.url");
                    if (url.contains("first/input")) return first;
                    assertEquals("jdbc:h2:file:second/input;TRACE_LEVEL_FILE=0;TRACE_LEVEL_SYSTEM_OUT=0;AUTO_SERVER=TRUE", url);
                    verify(first).close();
                    return second;
                });
            try {
                assertSame(first, factory("first/input"));
                assertSame(first, factory("first/input"));
                verify(first, never()).close();
                assertSame(second, factory("second/input"));
                assertSame(second, factory("second/input"));
                persistence.verify(() -> Persistence.createEntityManagerFactory(eq("starting-population"), anyMap()), times(2));
                closeFactory();
                closeFactory();
                verify(first, times(1)).close();
                verify(second, times(1)).close();
            } finally { closeFactory(); }
        }
    }

    @Test void externallyClosedFactoryIsRecreated() throws Exception {
        var first = mock(EntityManagerFactory.class);
        var second = mock(EntityManagerFactory.class);
        when(first.isOpen()).thenReturn(false);
        when(second.isOpen()).thenReturn(true);
        try (var persistence = mockStatic(Persistence.class)) {
            persistence.when(() -> Persistence.createEntityManagerFactory(eq("starting-population"), anyMap()))
                .thenReturn(first, second);
            try {
                assertSame(first, factory("same/input"));
                assertSame(second, factory("same/input"));
                verify(first, never()).close();
            } finally { closeFactory(); }
        }
    }

    @Test void replacementCreationFailureDoesNotRetainOldDatabase() throws Exception {
        var first = mock(EntityManagerFactory.class);
        var second = mock(EntityManagerFactory.class);
        when(first.isOpen()).thenReturn(true);
        when(second.isOpen()).thenReturn(true);
        try (var persistence = mockStatic(Persistence.class)) {
            persistence.when(() -> Persistence.createEntityManagerFactory(eq("starting-population"), anyMap()))
                .thenReturn(first).thenThrow(new IllegalStateException("creation failed")).thenReturn(second);
            try {
                assertSame(first, factory("first/input"));
                assertThrows(InvocationTargetException.class, () -> factory("second/input"));
                verify(first).close();
                assertSame(second, factory("second/input"));
            } finally { closeFactory(); }
        }
    }


    @Test void readAndSaveShareFactoryAndCleanUpTransactionsAndManagers() throws Exception {
        String original = SimPathsModel.getPersistDatabasePath();
        var factory = mock(EntityManagerFactory.class);
        when(factory.isOpen()).thenReturn(true);
        Method read = SimPathsModel.class.getDeclaredMethod("getProcessed");
        read.setAccessible(true);
        Method save = SimPathsModel.class.getDeclaredMethod("persistProcessed", Set.class,
                simpaths.model.enums.Country.class, int.class, int.class, boolean.class);
        save.setAccessible(true);
        try (var persistence = mockStatic(Persistence.class)) {
            persistence.when(() -> Persistence.createEntityManagerFactory(eq("starting-population"), anyMap()))
                    .thenReturn(factory);
            try {
                SimPathsModel.setPersistDatabasePath("shared/input");
                var model = new SimPathsModel(simpaths.model.enums.Country.UK, 2019);
                for (boolean writing : List.of(false, true)) {
                    for (boolean fail : List.of(false, true)) {
                        var em = mock(EntityManager.class);
                        var txn = mock(EntityTransaction.class);
                        when(factory.createEntityManager()).thenReturn(em);
                        when(em.getTransaction()).thenReturn(txn);
                        when(txn.isActive()).thenReturn(true);
                        if (writing && fail) doThrow(new IllegalStateException("save failed")).when(em).persist(any());
                        if (!writing) {
                            if (fail) when(em.createQuery(anyString())).thenThrow(new IllegalStateException("read failed"));
                            else {
                                var query = mock(Query.class);
                                when(em.createQuery(anyString())).thenReturn(query);
                                when(query.getResultList()).thenReturn(List.of());
                            }
                        }
                        org.junit.jupiter.api.function.Executable operation = () -> {
                            if (writing) save.invoke(model, Set.of(), simpaths.model.enums.Country.UK, 2019, 50000, false);
                            else assertNull(read.invoke(model));
                        };
                        if (fail) assertThrows(InvocationTargetException.class, operation);
                        else assertDoesNotThrow(operation);
                        if (writing && !fail) verify(txn).commit();
                        else { verify(txn).rollback(); verify(txn, never()).commit(); }
                        verify(em).close();
                        verify(factory, never()).close();
                    }
                }
                persistence.verify(() -> Persistence.createEntityManagerFactory(eq("starting-population"), anyMap()), times(1));
            } finally {
                closeFactory();
                SimPathsModel.setPersistDatabasePath(original);
            }
        }
    }
}
